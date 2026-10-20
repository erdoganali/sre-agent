"""Senaryo koşucusu (T1.9).

Enjekte et → alarmı bekle → raporu topla → karşılaştır → etiketle → temizle.

Kümeye `kubectl port-forward` ile bağlanır (Prometheus ve sre-agent); şirket DNS'ine
bağımlı değildir.
Senaryoların kendisi `scripts/scenario.sh` ile enjekte edilir ve temizlenir. Sonuç
`eval/results/<zaman>-<senaryo>.json` dosyasına yazılır.

Kullanım:
  python -m sre_agent.eval run <senaryo>... --context <kube-context> [--settle-min 30] [--with-load]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from sre_agent.eval.compare import Outcome, Scenario, Verdict, evaluate, load_scenario

POLL_S = 15
REPORT_WAIT_S = 240  # son alarmdan sonra raporun yazılması için
CLEAR_WAIT_S = 600  # önceki koşudan kalan beklenen alarmların susması için


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Endpoint:
    """kubectl port-forward ile bir Service'e HTTP erişimi; köprü koparsa yeniden kurulur."""

    RETRIES = 4

    def __init__(self, context: str, namespace: str, service: str, port: int):
        self.args = (context, namespace, service, port)
        self.proc: subprocess.Popen[bytes] | None = None
        self.client = httpx.Client(timeout=20)
        self.base = ""
        self._start()

    def _start(self) -> None:
        """Köprüyü kurar; kubectl'in kimlik yenilemesi yavaş olabilir: 3 deneme, her biri 60 sn."""
        context, namespace, service, port = self.args
        for _ in range(3):
            local = _free_port()
            self.proc = subprocess.Popen(
                [
                    "kubectl",
                    "--context",
                    context,
                    "-n",
                    namespace,
                    "port-forward",
                    f"svc/{service}",
                    f"{local}:{port}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            for _ in range(120):
                if self.proc.poll() is not None:
                    break
                with (
                    contextlib.suppress(OSError),
                    socket.create_connection(("127.0.0.1", local), 0.5),
                ):
                    self.base = f"http://127.0.0.1:{local}"
                    return
                time.sleep(0.5)
            err = self.proc.stderr.read().decode()[-300:] if self.proc.stderr else ""
            self.stop()
            log(f"  port-forward kurulamadı ({namespace}/{service}), yeniden deneniyor: {err}")
            time.sleep(5)
        raise RuntimeError(f"port-forward kurulamadı: {namespace}/{service}")

    def stop(self) -> None:
        if self.proc:
            if self.proc.poll() is None:
                self.proc.terminate()
            self.proc = None

    def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        for attempt in range(self.RETRIES):
            try:
                return self.client.request(method, self.base + path, **kw)
            except httpx.TransportError as exc:
                if attempt == self.RETRIES - 1:
                    raise
                log(f"  bağlantı koptu ({type(exc).__name__}), köprü yeniden kuruluyor")
                self.stop()
                time.sleep(2)
                self._start()
        raise AssertionError("ulaşılmaz")


class Cluster:
    """Prometheus ve sre-agent'a HTTP erişimi."""

    def __init__(self, prometheus: Endpoint, agent: Endpoint):
        self.prom = prometheus
        self.agent = agent

    def firing(self, alerts: list[str]) -> set[str]:
        if not alerts:
            return set()
        names = "|".join(alerts)
        r = self.prom.request(
            "GET",
            "/api/v1/query",
            params={"query": f'ALERTS{{alertname=~"{names}",alertstate="firing"}}'},
        )
        r.raise_for_status()
        return {x["metric"]["alertname"] for x in r.json()["data"]["result"]}

    def report_after(self, alerts: list[str], after: datetime) -> dict[str, Any] | None:
        r = self.agent.request("GET", "/reports", params={"include_test": "true", "limit": 100})
        r.raise_for_status()
        rows = [
            x
            for x in r.json()
            if x["alert"] in alerts and datetime.fromisoformat(x["generated_at"]) > after
        ]
        if not rows:
            return None
        first = min(rows, key=lambda x: x["generated_at"])
        full = self.agent.request("GET", f"/reports/{first['id']}")
        full.raise_for_status()
        report: dict[str, Any] = full.json()
        return report

    def label(self, report_id: str, scenario: Scenario) -> bool:
        r = self.agent.request(
            "POST",
            f"/reports/{report_id}/label",
            data={
                "category": scenario.expected.category,
                "root_cause": scenario.expected.root_cause,
                "labeled_by": f"senaryo:{scenario.id}",
            },
        )
        return r.status_code == 200


def _script(root: Path, action: str, name: str, context: str) -> tuple[bool, str]:
    p = subprocess.run(
        [str(root / "scripts/scenario.sh"), action, name, context],
        capture_output=True,
        text=True,
        timeout=600,
    )
    out = (p.stdout + p.stderr).strip()
    return p.returncode == 0, out.splitlines()[-1] if out else ""


def run_one(
    root: Path, scenario: Scenario, cluster: Cluster, context: str, *, with_load: bool, label: bool
) -> dict[str, Any]:
    exp = scenario.expected
    result: dict[str, Any] = {"scenario": scenario.id, "title": scenario.title}
    skip = None
    if exp.deferred:
        skip = f"ertelendi: {exp.deferred}"
    elif exp.requires and not with_load:
        skip = f"gerekli: {', '.join(exp.requires)} (--with-load ile koşulur)"
    if skip:
        out = Outcome(
            verdict=Verdict.SKIPPED,
            reasons=[skip],
            alerts_missing=[],
            evidence_found=[],
            evidence_missing=[],
            category_found=False,
            noise=[],
        )
        return result | {"outcome": out.model_dump()}

    # Önceki koşudan kalan beklenen alarmlar sussun (yoksa eski alarm yeni sayılır)
    deadline = time.monotonic() + CLEAR_WAIT_S
    while (still := cluster.firing(exp.alerts)) and time.monotonic() < deadline:
        log(f"  önceki alarm hâlâ çalıyor, bekleniyor: {', '.join(sorted(still))}")
        time.sleep(POLL_S * 2)

    started = datetime.now(UTC)
    t0 = time.monotonic()
    fired: dict[str, float] = {}
    report: dict[str, Any] | None = None
    try:
        ok, line = _script(root, "inject", scenario.id, context)
        log(f"  enjekte: {'OK' if ok else 'FAIL'} {line}")
        result["inject_ok"] = ok
        if ok:
            limit = t0 + (exp.alert_within_min + 5) * 60
            while time.monotonic() < limit and len(fired) < len(exp.alerts):
                time.sleep(POLL_S)
                for a in cluster.firing(exp.alerts) - set(fired):
                    fired[a] = round((time.monotonic() - t0) / 60, 1)
                    log(f"  alarm: {a} ({fired[a]} dk)")
            report_limit = time.monotonic() + REPORT_WAIT_S
            while report is None and time.monotonic() < report_limit and fired:
                report = cluster.report_after(exp.alerts, started)
                if report is None:
                    time.sleep(POLL_S)
    finally:
        cleaned, line = _script(root, "cleanup", scenario.id, context)
        log(f"  temizlik: {'OK' if cleaned else 'FAIL'} {line}")

    out = evaluate(exp, set(fired), report)
    labeled = report is not None and label and cluster.label(str(report["id"]), scenario)
    report_min = None
    if report:
        gen = datetime.fromisoformat(report["generated_at"])
        report_min = round((gen - started).total_seconds() / 60, 1)
    return result | {
        "started_at": started.isoformat(),
        "alerts_fired_min": fired,
        "report_id": report["id"] if report else None,
        "report_min": report_min,
        "blind_spots": list(report.get("blind_spots", [])) if report else [],
        "labeled": labeled,
        "cleanup_ok": cleaned,
        "cleanup": line,
        "outcome": out.model_dump(),
    }


def summary_line(r: dict[str, Any]) -> str:
    o = r["outcome"]
    fired = ", ".join(f"{a} {m} dk" for a, m in (r.get("alerts_fired_min") or {}).items()) or "—"
    rep = f"{r['report_min']} dk" if r.get("report_min") is not None else "—"
    return (
        f"{r['scenario']:<20} {o['verdict']:<8} alarm: {fired:<40} rapor: {rep:<8} "
        f"{'; '.join(o['reasons'])}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sre_agent.eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="senaryoları koş")
    run.add_argument("scenarios", nargs="*", help="boşsa hepsi")
    run.add_argument("--context", required=True)
    run.add_argument("--root", type=Path, default=Path("."))
    run.add_argument("--settle-min", type=float, default=30, help="senaryolar arası bekleme")
    run.add_argument("--with-load", action="store_true", help="yük gerektiren senaryoları da koş")
    run.add_argument("--no-label", action="store_true", help="raporu otomatik etiketleme")
    run.add_argument("--results", type=Path, default=Path("eval/results"))
    args = parser.parse_args(argv)

    root: Path = args.root.resolve()
    names = args.scenarios or sorted(
        p.name for p in (root / "scenarios").iterdir() if (p / "expected.yaml").is_file()
    )
    args.results.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%MZ")
    results: list[dict[str, Any]] = []
    prom = Endpoint(args.context, "monitoring", "monitoring-kube-prometheus-prometheus", 9090)
    agent = Endpoint(args.context, "sre-agent", "sre-agent", 8080)
    try:
        cluster = Cluster(prom, agent)
        for i, name in enumerate(names):
            scenario = load_scenario(root / "scenarios" / name)
            log(f"## {name}: {scenario.title}")
            r = run_one(
                root,
                scenario,
                cluster,
                args.context,
                with_load=args.with_load,
                label=not args.no_label,
            )
            (args.results / f"{stamp}-{name}.json").write_text(
                json.dumps(r, ensure_ascii=False, indent=1)
            )
            results.append(r)
            log(summary_line(r))
            ran = r["outcome"]["verdict"] != Verdict.SKIPPED
            if ran and i < len(names) - 1 and args.settle_min > 0:
                log(f"  sonraki senaryodan önce {args.settle_min:g} dk bekleniyor")
                time.sleep(args.settle_min * 60)
    finally:
        prom.stop()
        agent.stop()

    print("\n== Sonuç")
    for r in results:
        print(summary_line(r))
    counts = {v: sum(r["outcome"]["verdict"] == v for r in results) for v in Verdict}
    print(", ".join(f"{v}: {n}" for v, n in counts.items() if n))
    return 0 if not counts[Verdict.FAIL] else 1


if __name__ == "__main__":
    sys.exit(main())
