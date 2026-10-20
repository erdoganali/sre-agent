"""MCP sunucusunu başlatır (ADR-030): bir süreç, bir yetenek.

  python -m sre_agent.mcp <metrics|logs|traces|k8s|product> [--config F] [--base D] [--port N]
      [--allow-host H ...]

Adresler config'ten (prometheus_url, loki_url, tempo_url; paketler). Kubernetes'e yalnız k8s ve
product sunucuları bağlanır (küme içinde kendi ServiceAccount'larıyla). Streamable HTTP, `/mcp`;
Host başlığı yalnız `--allow-host` ile verilen adlar (Service adı) ve localhost için kabul edilir.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from lightkube import AsyncClient, KubeConfig
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from sre_agent.adapters.k8s_changes import K8sChangeSource
from sre_agent.adapters.kubernetes import KubernetesReader
from sre_agent.adapters.loki import LokiLogs
from sre_agent.adapters.prometheus import PrometheusMetrics
from sre_agent.adapters.tempo import TempoTraces
from sre_agent.adapters.vector_store import KnowledgeBase
from sre_agent.config import AppConfig, load_app_config
from sre_agent.mcp import k8s, logs, metrics, product, traces
from sre_agent.packs import CheckRegistry, load_packs
from sre_agent.ports import Capability
from sre_agent.wiring import build_checks

SERVERS = ("metrics", "logs", "traces", "k8s", "product")


def _need(value: str | None, what: str) -> str:
    if not value:
        raise SystemExit(f"config'te {what} yok")
    return value


def _k8s(cfg: AppConfig, registry: CheckRegistry) -> KubernetesReader:
    client = (
        AsyncClient(KubeConfig.from_file("~/.kube/config").get(context_name=cfg.kube_context))
        if cfg.kube_context
        else AsyncClient()
    )
    return KubernetesReader(client=client, extra_custom_kinds=registry.custom_kinds)


def build_server(name: str, cfg: AppConfig, base: Path) -> MCPServer:
    if name == "metrics":
        return metrics.build(PrometheusMetrics(_need(cfg.prometheus_url, "prometheus_url")))
    if name == "logs":
        return logs.build(LokiLogs(_need(cfg.loki_url, "loki_url")))
    if name == "traces":
        return traces.build(TempoTraces(_need(cfg.tempo_url, "tempo_url")))
    registry = CheckRegistry(load_packs(cfg, base))
    reader = _k8s(cfg, registry)
    prom = PrometheusMetrics(cfg.prometheus_url) if cfg.prometheus_url else None
    if name == "k8s":
        return k8s.build(reader, prom, K8sChangeSource(reader))
    ports: dict[Capability, Any] = {Capability.K8S: reader}
    if prom is not None:
        ports[Capability.METRICS] = prom
    checks = {c.name: c for c in build_checks(sorted(registry.product_checks), registry, cfg)}
    topologies = {p.manifest.name: p.topology for p in registry.packs if p.topology}
    kb = KnowledgeBase(cfg.knowledge) if cfg.knowledge.uri else None
    return product.build(checks, ports, topologies, kb)


def security(hosts: list[str]) -> TransportSecuritySettings:
    names = [*hosts, "localhost", "127.0.0.1"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*names, *(f"{h}:*" for h in names)],
        allowed_origins=[],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sre_agent.mcp")
    parser.add_argument("server", choices=SERVERS)
    parser.add_argument("--config", type=Path, default=Path("/etc/sre-agent/config.yaml"))
    parser.add_argument("--base", type=Path, default=Path("/app"))
    parser.add_argument("--host", default="0.0.0.0")  # küme içi Service arkasında
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--allow-host", action="append", default=[])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    server = build_server(args.server, load_app_config(args.config), args.base)
    server.run(
        "streamable-http",
        host=args.host,
        port=args.port,
        stateless_http=True,
        transport_security=security(args.allow_host),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
