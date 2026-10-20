import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from fakes import END, FakeK8s, FakeMetrics
from sre_agent.adapters.grafana import GrafanaAnnotations
from sre_agent.checks import Evidence, Status, Window
from sre_agent.checks.builtin import GoldenSignalsBaseline, TlsCertificateExpiry
from sre_agent.config import AppConfig, TlsEndpoint
from sre_agent.packs import CheckRegistry
from sre_agent.ports import Capability, Sample
from sre_agent.report import AlertRef, build_report, order_evidence, to_markdown
from sre_agent.service.app import create_app
from sre_agent.service.runner import ReportRunner
from sre_agent.wiring import build_checks

STARTS = END - timedelta(minutes=5)


class FakeAnnotator:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[datetime, str, list[str]]] = []
        self.fail = fail

    async def annotate(self, at: datetime, text: str, tags: list[str]) -> int:
        if self.fail:
            raise RuntimeError("grafana kapalı")
        self.calls.append((at, text, tags))
        return 42


def restarted_pod() -> dict[str, Any]:
    finished = (END - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    last = {"terminated": {"reason": "OOMKilled", "exitCode": 137, "finishedAt": finished}}
    return {
        "metadata": {"name": "web"},
        "status": {"containerStatuses": [{"name": "app", "restartCount": 1, "lastState": last}]},
    }


def runner(annotator: FakeAnnotator | None = None, **cfg: Any) -> ReportRunner:
    config = AppConfig(default_checks=["pod_restarts", "kafka_consumer_lag"], **cfg)
    ports = {
        Capability.K8S: FakeK8s({"Pod": [restarted_pod()]}),
        Capability.METRICS: FakeMetrics(resolver=lambda q: [Sample({}, 0.0)]),
    }
    return ReportRunner(config, CheckRegistry(), ports, annotator, now=lambda: END)


def payload(*fingerprints: str, status: str = "firing") -> dict[str, Any]:
    return {
        "alerts": [
            {
                "status": status,
                "fingerprint": fp,
                "labels": {"alertname": "PodBroken", "namespace": "sre-lab"},
                "startsAt": STARTS.isoformat().replace("+00:00", "Z"),
            }
            for fp in fingerprints
        ]
    }


async def test_alert_produces_report_and_annotation() -> None:
    ann = FakeAnnotator()
    r = runner(ann, report_base_url="http://sre")
    assert r.submit(payload("fp1")) == ["fp1"]
    await r.wait_idle()
    (report,) = r.reports.values()
    assert report.annotation_id == 42
    assert [e.check for e in report.anomalies] == ["pod_restarts"]
    assert report.window.start == STARTS - timedelta(minutes=30) and report.window.end == END
    at, text, tags = ann.calls[0]
    assert at == STARTS and "1 anomali — pod_restarts:" in text and "\n" not in text
    assert text.endswith(f"http://sre/reports/{report.id}.md")
    assert tags == ["sre-agent", "PodBroken", "namespace=sre-lab"]


async def test_dedupe_and_resolved_are_skipped() -> None:
    r = runner()
    assert r.submit(payload("fp1")) == ["fp1"]
    assert r.submit(payload("fp1")) == []  # 10 dk içinde tekrar
    assert r.submit(payload("fp2", status="resolved")) == []
    await r.wait_idle()
    assert len(r.reports) == 1


async def test_annotation_failure_does_not_lose_report() -> None:
    r = runner(FakeAnnotator(fail=True))
    r.submit(payload("fp1"))
    await r.wait_idle()
    (report,) = r.reports.values()
    assert report.annotation_id is None


async def test_concurrency_is_limited() -> None:
    r = runner(max_concurrent_runs=2)
    running = peak = 0
    original = r.run

    async def slow(alert: dict[str, Any], fp: str) -> Any:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return await original(alert, fp)

    r.run = slow  # type: ignore[method-assign]
    r.submit(payload(*[f"fp{i}" for i in range(6)]))
    await r.wait_idle()
    assert peak == 2 and len(r.reports) == 6


def test_selection_prefers_pack_routes_then_defaults() -> None:
    r = runner()
    assert r.select("Anything") == ["pod_restarts", "kafka_consumer_lag"]
    core = ReportRunner(AppConfig(), CheckRegistry(), {})
    assert "tls_certificate_expiry" in core.select("Anything")


async def test_http_endpoints() -> None:
    r = runner()
    app = create_app(r)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/healthz")).json() == {"status": "ok"}
        resp = await c.post("/alerts", json=payload("fp1"))
        assert resp.status_code == 202 and resp.json() == {"accepted": ["fp1"]}
        await r.wait_idle()
        listing = (await c.get("/reports")).json()
        rid = listing[0]["id"]
        assert listing[0]["anomalies"] == ["pod_restarts"]
        assert (await c.get(f"/reports/{rid}")).json()["alert"]["name"] == "PodBroken"
        md = (await c.get(f"/reports/{rid}.md")).text
        assert md.startswith("# PodBroken") and "## Altyapı" in md
        assert (await c.get("/reports/nope")).status_code == 404
        too_many = {"alerts": [{"status": "firing"}] * 201}
        assert (await c.post("/alerts", json=too_many)).status_code == 413


def ev(check: str, categories: tuple[str, ...], status: Status) -> Evidence:
    window = Window(start=END - timedelta(hours=1), end=END)
    return Evidence(
        check=check,
        capability=Capability.K8S,
        categories=categories,
        status=status,
        summary=check,
        window=window,
    )


def test_evidence_ordered_by_taxonomy_group_then_status() -> None:
    ordered = order_evidence(
        [
            ev("sec", ("security",), Status.ANOMALY),
            ev("cap_ok", ("capacity_performance",), Status.OK),
            ev("cap_bad", ("capacity_performance",), Status.ANOMALY),
            ev("release", ("application_release",), Status.NO_DATA),
        ]
    )
    assert [e.check for e in ordered] == ["release", "cap_bad", "cap_ok", "sec"]


def test_markdown_shows_query_for_anomalies() -> None:
    e = ev("cap_bad", ("capacity_performance",), Status.ANOMALY).model_copy(update={"query": "up"})
    report = build_report(
        "r1",
        AlertRef(name="A", fingerprint="f", labels={"namespace": "x"}, starts_at=END),
        e.window,
        END,
        10,
        [e],
    )
    md = to_markdown(report)
    assert "🔴 **cap_bad**" in md and "  up" in md and "Capacity/Performance (1)" in md


def test_build_checks_passes_overrides_signals_and_endpoints() -> None:
    cfg = AppConfig(
        query_overrides={"ephemeral_storage_usage": {"pod": "x{{ns='{ns}'}}"}},
        tls_endpoints=[TlsEndpoint(host="kafka", port=9095)],
    )
    checks = build_checks(
        ["ephemeral_storage_usage", "golden_signals_baseline", "tls_certificate_expiry"],
        CheckRegistry(),
        cfg,
    )
    assert checks[0].q["pod"] == "x{{ns='{ns}'}}"
    assert isinstance(checks[1], GoldenSignalsBaseline) and checks[1].signals
    assert isinstance(checks[2], TlsCertificateExpiry)
    assert checks[2].endpoints == (("kafka", 9095, None),)


async def test_grafana_annotation_request() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"id": 7, "message": "Annotation added"})

    client = httpx.AsyncClient(
        base_url="http://g",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer t"},
    )
    at = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)
    assert await GrafanaAnnotations("http://g", "t", client=client).annotate(at, "x", ["a"]) == 7
    body = seen[0].read()
    assert seen[0].url.path == "/api/annotations" and b'"time":1790503200000' in body
    assert seen[0].headers["Authorization"] == "Bearer t"


def test_build_ports_without_kubernetes_still_starts(monkeypatch: Any, tmp_path: Any) -> None:
    from sre_agent.wiring import build_ports

    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "")
    monkeypatch.setenv("HOME", str(tmp_path))  # kubeconfig yok
    monkeypatch.delenv("KUBECONFIG", raising=False)
    ports = build_ports(AppConfig(prometheus_url="http://p"), CheckRegistry())
    assert Capability.METRICS in ports and Capability.K8S not in ports


async def test_future_starts_at_is_clamped_to_now() -> None:
    ann = FakeAnnotator()
    r = runner(ann)
    body = payload("fp1")
    body["alerts"][0]["startsAt"] = (END + timedelta(minutes=5)).isoformat()
    r.submit(body)
    await r.wait_idle()
    assert ann.calls[0][0] == END
    (report,) = r.reports.values()
    assert report.window.start == END - timedelta(minutes=30)


async def test_synthetic_alerts_are_tagged_test_and_quiet_report_reads_well() -> None:
    ann = FakeAnnotator()
    config = AppConfig(default_checks=["kafka_consumer_lag"])
    ports = {Capability.METRICS: FakeMetrics(resolver=lambda q: [Sample({}, 0.0)])}
    r = ReportRunner(config, CheckRegistry(), ports, ann, now=lambda: END)
    body = payload("fp1")
    body["alerts"][0]["labels"]["test"] = "true"
    r.submit(body)
    await r.wait_idle()
    _, text, tags = ann.calls[0]
    assert tags[-1] == "test"
    assert text == "SRE raporu — PodBroken: anomali yok (1 kontrol)"


def test_markdown_group_headings_not_repeated_for_multi_category_checks() -> None:
    from sre_agent.report import AlertRef, build_report, to_markdown

    evidence = [
        ev("crash", ("capacity_performance", "application_service_bug"), Status.ANOMALY),
        ev("restarts", ("capacity_performance",), Status.ANOMALY),
        ev("errors", ("application_service_bug",), Status.OK),
    ]
    r = build_report(
        "r",
        AlertRef(name="A", fingerprint="f", labels={}, starts_at=END),
        Window(start=END - timedelta(hours=1), end=END),
        END,
        1,
        evidence,
    )
    heads = [line for line in to_markdown(r).splitlines() if line.startswith("## ")]
    assert heads == ["## Uygulama", "## Altyapı"]
