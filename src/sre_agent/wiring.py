"""Yapılandırmadan adaptörleri ve kontrolleri kurar (ortama özel seçimler burada toplanır)."""

from __future__ import annotations

import inspect
import logging
from typing import Any

from lightkube import AsyncClient, ConfigError, KubeConfig

from sre_agent.adapters.argocd import ArgoCdChangeSource
from sre_agent.adapters.k8s_changes import K8sChangeSource
from sre_agent.adapters.kubernetes import KubernetesReader
from sre_agent.adapters.loki import LokiLogs
from sre_agent.adapters.prometheus import PrometheusMetrics
from sre_agent.adapters.tempo import TempoTraces
from sre_agent.adapters.tls import TlsProbe
from sre_agent.checks import Check
from sre_agent.config import AppConfig
from sre_agent.packs import CheckRegistry
from sre_agent.ports import Capability, ChangePort, CombinedChanges

log = logging.getLogger("sre_agent.wiring")


def build_ports(cfg: AppConfig, registry: CheckRegistry) -> dict[Capability, Any]:
    ports: dict[Capability, Any] = {Capability.TLS: TlsProbe()}
    if cfg.prometheus_url:
        ports[Capability.METRICS] = PrometheusMetrics(cfg.prometheus_url)
    if cfg.loki_url:
        ports[Capability.LOGS] = LokiLogs(cfg.loki_url)
    if cfg.tempo_url:
        ports[Capability.TRACES] = TempoTraces(cfg.tempo_url)
    # Cluster içinde ServiceAccount; dışarıda kubeconfig context'i. Kurulamazsa servis yine açılır,
    # Kubernetes'e ihtiyaç duyan kontroller "adaptör yok" (error) döner.
    try:
        client = (
            AsyncClient(KubeConfig.from_file("~/.kube/config").get(context_name=cfg.kube_context))
            if cfg.kube_context
            else AsyncClient()
        )
    except ConfigError as exc:
        log.warning("Kubernetes bağlantısı kurulamadı, k8s/change yetenekleri kapalı: %s", exc)
        return ports
    k8s = KubernetesReader(client=client, extra_custom_kinds=registry.custom_kinds)
    ports[Capability.K8S] = k8s
    sources: list[ChangePort] = [K8sChangeSource(k8s)]
    if cfg.argocd_namespace:
        sources.append(ArgoCdChangeSource(k8s, cfg.argocd_namespace))
    ports[Capability.CHANGE] = CombinedChanges(*sources)
    return ports


def build_checks(names: list[str], registry: CheckRegistry, cfg: AppConfig) -> list[Check]:
    """Kontrolleri kurar: sorgu değişiklikleri, paket sinyalleri ve TLS uç noktaları verilir."""
    extras: dict[str, Any] = {
        "signals": registry.signals,
        "endpoints": tuple((e.host, e.port, e.sni) for e in cfg.tls_endpoints),
    }
    out: list[Check] = []
    for name in names:
        cls = registry.checks[name]
        params = inspect.signature(cls.__init__).parameters
        kwargs = {k: v for k, v in extras.items() if k in params}
        out.append(cls(queries=cfg.query_overrides.get(name), **kwargs))
    return out
