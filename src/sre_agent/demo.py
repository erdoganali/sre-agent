"""Demo mode (ADR-034): küme ve Postgres olmadan, kayıtlı bir incident üzerinde investigation.

  make demo                        # = uv run python -m sre_agent.demo
  python -m sre_agent.demo [--fixture demo/incident.json] [--out demo/out]

Tool cevapları kayıttan gelir (graph/replay.py), knowledge base yerel vektör veritabanı dosyasıdır,
model `.env`'deki OpenAI uyumlu uç noktadır. Anahtar yoksa model çağrılmaz; rapor kurallarla
(Faz 1 kanıtı) üretilir. Çıktı: <out>/report.md ve <out>/investigation.json.
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
from sre_agent.graph import render
from sre_agent.graph.replay import MemoryStore, Replay
from sre_agent.graph.wiring import build_investigator
from sre_agent.packs import CheckRegistry, load_packs
from sre_agent.report import Report, to_markdown

KEY_ENV = "DEMO_MODEL_KEY"


def load_env(path: Path) -> None:
    """`AD=değer` satırları; ortamda zaten olan değişken ezilmez."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        name, eq, value = line.strip().partition("=")
        if eq and name and not name.startswith("#"):
            os.environ.setdefault(name.strip(), value.strip().strip("'\""))


def index_knowledge(cfg: Any, base: Path) -> int:
    from sre_agent.adapters.vector_store import KnowledgeBase
    from sre_agent.knowledge import collect

    Path(cfg.knowledge.uri).parent.mkdir(parents=True, exist_ok=True)
    packs = {pk.manifest.name: pk.path for pk in load_packs(cfg, base)}
    return KnowledgeBase(cfg.knowledge).rebuild(collect(packs))


async def run(args: argparse.Namespace) -> int:
    load_env(args.env)
    replay = Replay.load(args.fixture)
    incident = replay.data["incident_id"]
    cfg = load_app_config(args.config)
    ic = cfg.investigation.model_copy(
        update={"mcp_servers": {s: f"replay:{s}" for s in replay.servers}}
    )
    if not os.environ.get(KEY_ENV):
        print(f"{KEY_ENV} yok: model çağrılmaz, sonuç kurallarla üretilir (.env.example)")
        ic = ic.model_copy(update={"models_file": ""})
    cfg = cfg.model_copy(update={"investigation": ic})
    if cfg.knowledge.uri:
        print(f"knowledge base: {index_knowledge(cfg, args.base)} doc → {cfg.knowledge.uri}")

    store = MemoryStore({incident: replay.data["reports"]})
    registry = CheckRegistry(load_packs(cfg, args.base))
    inv = build_investigator(cfg, registry, store, None, replay.connect)  # type: ignore[arg-type]
    assert inv is not None
    print(f"incident {incident}: {len(replay.data['reports'])} rapor, investigation koşuyor…")
    result = await inv.run(incident)

    args.out.mkdir(parents=True, exist_ok=True)
    md = render.attach(to_markdown(Report.model_validate(replay.data["reports"][0])), result, "on")
    (args.out / "report.md").write_text(md)
    (args.out / "investigation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=str)
    )
    rep, ev = result["report"] or {}, result["evaluation"] or {}
    calls = sum(len(f.get("tools_used") or []) for f in (result["findings"] or {}).values())
    print(f"specialist'ler: {', '.join(result['specialists'] or [])} ({calls} tool çağrısı)")
    print(f"sonuç ({rep.get('source')}): {rep.get('category')}, güven {ev.get('confidence')}")
    print(f"  {rep.get('summary')}")
    print(f"token: {result['budget'].get('used')}")
    if replay.misses:
        print(f"kayıtta birebir olmayan {len(replay.misses)} çağrı en yakın kayıtla cevaplandı")
    if result["errors"]:
        print("hatalar:", json.dumps(result["errors"], ensure_ascii=False))
    print(f"rapor: {args.out / 'report.md'}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="sre_agent.demo")
    p.add_argument("--fixture", type=Path, default=Path("demo/incident.json"))
    p.add_argument("--config", type=Path, default=Path("demo/config.yaml"))
    p.add_argument("--base", type=Path, default=Path("."))
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--out", type=Path, default=Path("demo/out"))
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
