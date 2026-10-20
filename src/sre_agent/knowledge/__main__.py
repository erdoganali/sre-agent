"""Bilgi tabanı: indeksleme ve deneme araması.

python -m sre_agent.knowledge index  [--config F] [--base D] [--uri U]
python -m sre_agent.knowledge search "etcd session expired" [--kind failure]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sre_agent.adapters.vector_store import KnowledgeBase
from sre_agent.config import load_app_config
from sre_agent.knowledge import KnowledgeConfig, collect
from sre_agent.packs import load_packs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sre_agent.knowledge")
    p.add_argument("action", choices=["index", "search"])
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--config", type=Path, default=Path("/etc/sre-agent/config.yaml"))
    p.add_argument("--base", type=Path, default=Path("/app"))
    p.add_argument("--uri", help="config'teki knowledge.uri yerine (ör. ./knowledge.db)")
    p.add_argument("--kind")
    p.add_argument("--limit", type=int, default=5)
    args = p.parse_args(argv)
    cfg = load_app_config(args.config)
    kc: KnowledgeConfig = cfg.knowledge
    if args.uri:
        kc = kc.model_copy(update={"uri": args.uri})
    kb = KnowledgeBase(kc)
    if args.action == "index":
        packs = {pk.manifest.name: pk.path for pk in load_packs(cfg, args.base)}
        n = kb.rebuild(collect(packs))
        print(f"{kc.collection}: {n} belge yazıldı ({', '.join(packs) or 'paket yok'} + taksonomi)")
        return 0
    for h in kb.search_sync(args.query, args.limit, args.kind):
        print(f"{h.score:6.2f}  {h.doc.kind:<8} {h.doc.id}  {h.doc.title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
