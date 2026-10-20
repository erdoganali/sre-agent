"""Çalıştırma: python -m sre_agent.service --config config/gke.yaml [--base .]"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from sre_agent.adapters.grafana import GrafanaAnnotations
from sre_agent.config import load_app_config
from sre_agent.decisions.wiring import build_decider
from sre_agent.graph.wiring import build_investigator
from sre_agent.llm.telemetry import setup_tracing
from sre_agent.packs import CheckRegistry, load_packs
from sre_agent.service.app import create_app
from sre_agent.service.runner import ReportRunner
from sre_agent.store.migrate import upgrade_async
from sre_agent.store.postgres import PostgresReportStore
from sre_agent.wiring import build_ports


def main() -> None:
    parser = argparse.ArgumentParser(prog="sre-agent")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--base", type=Path, default=Path("."), help="paket yollarının kökü")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "httpx2"):  # her istek INFO'da loglanmasın
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if setup_tracing():  # OTEL_EXPORTER_OTLP_ENDPOINT varsa model span'ları Tempo'ya (T2.3)
        logging.getLogger(__name__).info("OTel trace dışa aktarımı açık")
    cfg = load_app_config(args.config)
    registry = CheckRegistry(load_packs(cfg, args.base))
    token = os.environ.get("GRAFANA_TOKEN")
    annotator = GrafanaAnnotations(cfg.grafana_url, token) if cfg.grafana_url and token else None
    store = None
    migrate = None
    database_url = os.environ.get("DATABASE_URL")
    if database_url:  # yoksa raporlar yalnızca bellekte (ADR-023)
        store = PostgresReportStore(database_url)
        url = database_url

        # Şema servis açıldıktan sonra arka planda kurulur; Postgres henüz hazır olmayabilir.
        async def migrate() -> None:
            await upgrade_async(url)

    runner = ReportRunner(
        cfg,
        registry,
        build_ports(cfg, registry),
        annotator,
        store=store,
        migrate=migrate,
        decider=build_decider(cfg.decisions),  # T2.5; mod off ise yok
        investigator=build_investigator(cfg, registry, store, database_url),  # T3.2
    )
    uvicorn.run(create_app(runner), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
