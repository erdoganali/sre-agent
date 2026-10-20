"""Investigation graph'ı elle koşturur (T3.2) ve specialist'leri ölçer (T3.4).

  python -m sre_agent.graph run --incident <id> [--config /etc/sre-agent/config.yaml]
      [--record kayit.json]   # MCP cevapları + incident raporları (demo mode replay'i, ADR-034)
  python -m sre_agent.graph eval-specialists --from-file export.json --mcp k8s=http://.../mcp ...
      [--incidents ID ...] [--limit N] [--out sonuc.json]

Pod içinde çalışır (DATABASE_URL pod'da). Sonuç incident'a yazılır ve özeti basılır.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from sre_agent.config import load_app_config
from sre_agent.graph.investigator import GraphMode
from sre_agent.graph.wiring import build_investigator
from sre_agent.packs import CheckRegistry, load_packs
from sre_agent.store.postgres import PostgresReportStore


async def run(incident: str, config: Path, base: Path, record: Path | None = None) -> int:
    cfg = load_app_config(config)
    if cfg.investigation.mode is GraphMode.OFF:
        cfg = cfg.model_copy(
            update={
                "investigation": cfg.investigation.model_copy(update={"mode": GraphMode.SHADOW})
            }
        )
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL yok")
        return 2
    from opentelemetry import trace

    from sre_agent.llm.telemetry import setup_tracing

    tracing = setup_tracing()  # servisle aynı: OTEL_EXPORTER_OTLP_ENDPOINT varsa Tempo'ya
    store = PostgresReportStore(url)
    recorder = None
    if record:
        from mcp import Client

        from sre_agent.graph.replay import Recorder

        urls = dict(cfg.investigation.mcp_servers)
        recorder = Recorder(lambda server: Client(urls[server]))
    connect = recorder.connect if recorder else None
    inv = build_investigator(cfg, CheckRegistry(load_packs(cfg, base)), store, url, connect)
    assert inv is not None
    result = await inv.run(incident)
    if recorder and record:
        recorder.dump(record, incident, await store.incident_reports(incident))
        print(f"kayıt: {record} ({len(recorder.calls)} tool çağrısı)")
    await store.aclose()
    if tracing:  # komut biter bitmez çıkar: bekleyen span'lar gönderilsin
        trace.get_tracer_provider().shutdown()  # type: ignore[attr-defined]
    rep = result["report"] or {}
    print(f"specialist'ler: {', '.join(result['specialists'] or [])}")
    for name, f in (result["findings"] or {}).items():
        print(f"  {name:<8} [{f.get('confidence')}] {f.get('summary')} → {f.get('suspects')}")
    corr = result["correlation"] or {}
    print(f"correlate ({corr.get('method')}): {corr.get('category')} — {corr.get('root_cause')}")
    ev = result["evaluation"] or {}
    print(f"evaluate: güven={ev.get('confidence')} cross_validated={ev.get('cross_validated')}")
    print(f"rapor ({rep.get('source')}, kesin={rep.get('certain')}): {rep.get('category')}")
    print(f"  {rep.get('summary')}")
    if result["errors"]:
        print("hatalar:", json.dumps(result["errors"], ensure_ascii=False))
    print(f"bütçe: {result['budget']}")
    return 0


async def eval_specialists(args: argparse.Namespace) -> int:
    import collections
    import time

    from sre_agent.graph.build import GraphConfig
    from sre_agent.graph.investigator import (
        InvestigationConfig,
        Investigator,
        model_reasoner_factory,
        model_specialist_factory,
    )
    from sre_agent.graph.specialist_eval import label_of, load_expectations, render, rows_for

    data = json.loads(args.from_file.read_text())
    by: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in data:
        if r.get("incident_id"):
            by[r["incident_id"]].append(r)
    ids = args.incidents or sorted(by, key=lambda i: -len(by[i]))[: args.limit]

    class Mem:
        async def incident_reports(self, i: str) -> list[dict[str, Any]]:
            return [r["report"] for r in by[i]]

        async def save_investigation(self, *a: Any) -> None:
            return None

    cfg = load_app_config(args.config)
    registry = CheckRegistry(load_packs(cfg, args.base))
    urls = dict(kv.split("=", 1) for kv in args.mcp)
    ic = InvestigationConfig(mode=GraphMode.SHADOW, models_file=str(args.models), mcp_servers=urls)
    reasoner = model_reasoner_factory(ic)
    specialists = model_specialist_factory(ic, registry.specialist_guides)
    if reasoner is None or specialists is None:
        print("model kataloğu ya da --mcp yok")
        return 2
    inv = Investigator(
        ic,
        Mem(),  # type: ignore[arg-type]
        reasoner,
        graph_cfg=GraphConfig(
            product_checks=registry.product_checks, product_aliases=registry.product_aliases
        ),
        specialist_factory=specialists,
    )
    exp = load_expectations(args.expectations)
    rows, reports, raw = [], {}, {}
    total = 0
    for i in ids:
        t0 = time.monotonic()
        res = await inv.run(i)
        label = label_of(by[i])
        new = rows_for(i, label, res, exp)
        rows += new
        reports[i] = res.get("report") or {}
        raw[i] = res | {"label": label, "seconds": round(time.monotonic() - t0, 1)}
        used = res["budget"].get("used") or 0
        total += used
        print(f"{i}: {raw[i]['seconds']} sn, {used} token (toplam {total})", file=sys.stderr)
        # Her olay bitince yazılır: ölçüm yarıda durdurulsa da o ana kadarki sonuç kalır
        print(render(new, {i: reports[i]}).split("\n\n")[0].split("\n", 2)[2], flush=True)
        if args.out:
            args.out.write_text(json.dumps(raw, ensure_ascii=False, indent=1, default=str))
        if args.max_tokens and total >= args.max_tokens:
            print(f"token sınırı ({args.max_tokens}) doldu; ölçüm durdu", file=sys.stderr)
            break
    print("\n" + render(rows, reports))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="sre_agent.graph")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="bir incident üzerinde graph'ı koştur")
    r.add_argument("--incident", required=True)
    r.add_argument("--config", type=Path, default=Path("/etc/sre-agent/config.yaml"))
    r.add_argument("--base", type=Path, default=Path("."))
    r.add_argument("--record", type=Path, help="MCP cevaplarını ve raporları bu dosyaya yaz")
    e = sub.add_parser("eval-specialists", help="etiketli olaylarda specialist ölçümü (T3.4)")
    e.add_argument("--from-file", type=Path, required=True, help="decisions export dosyası")
    e.add_argument("--mcp", action="append", default=[], help="sunucu=adres, ör. k8s=http://x/mcp")
    e.add_argument("--models", type=Path, default=Path("config/models.yaml"))
    e.add_argument("--config", type=Path, default=Path("config/gke.yaml"))
    e.add_argument("--base", type=Path, default=Path("."))
    e.add_argument("--expectations", type=Path, default=Path("eval/specialist-expectations.yaml"))
    e.add_argument("--incidents", nargs="*")
    e.add_argument("--limit", type=int, default=5)
    e.add_argument("--out", type=Path)
    e.add_argument("--max-tokens", type=int, default=400_000, help="ölçümün toplam token sınırı")
    args = parser.parse_args()
    if args.cmd == "eval-specialists":
        return asyncio.run(eval_specialists(args))
    return asyncio.run(run(args.incident, args.config, args.base, args.record))


if __name__ == "__main__":
    sys.exit(main())
