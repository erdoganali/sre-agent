"""Yetenek portları: kontroller araca değil bu arayüzlere bağlanır.

Portlar yalnızca okuma metotları tanımlar. Ortama özel kod (Prometheus, Loki, Tempo,
Kubernetes) `sre_agent.adapters` altındaki uygulamalardadır.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable


class Capability(StrEnum):
    METRICS = "metrics"
    LOGS = "logs"
    TRACES = "traces"
    K8S = "k8s"
    CHANGE = "change"
    TLS = "tls"


@dataclass(frozen=True)
class Sample:
    """Anlık sorgu sonucu: etiketler ve tek değer."""

    labels: dict[str, str]
    value: float


@dataclass(frozen=True)
class Series:
    """Aralık sorgusu sonucu: etiketler ve (zaman, değer) noktaları."""

    labels: dict[str, str]
    points: list[tuple[datetime, float]]


@dataclass(frozen=True)
class LogLine:
    labels: dict[str, str]
    ts: datetime
    line: str


@dataclass(frozen=True)
class TraceSummary:
    trace_id: str
    root_service: str | None
    root_name: str | None
    start: datetime | None
    duration_ms: float | None
    attributes: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MetricsPort(Protocol):
    async def query(self, promql: str, at: datetime) -> list[Sample]: ...

    async def query_range(
        self, promql: str, start: datetime, end: datetime, step_s: int
    ) -> list[Series]: ...


@runtime_checkable
class LogsPort(Protocol):
    async def query_range(
        self, logql: str, start: datetime, end: datetime, limit: int
    ) -> list[LogLine]: ...


@runtime_checkable
class TracesPort(Protocol):
    async def search(
        self, traceql: str, start: datetime, end: datetime, limit: int
    ) -> list[TraceSummary]: ...


@dataclass(frozen=True)
class Span:
    """Bir trace'in tek span'ı (ayrıntı görünümü; T3.3 traces sunucusu)."""

    span_id: str
    parent_id: str | None
    service: str | None
    name: str
    start: datetime
    duration_ms: float
    error: bool
    status_message: str | None = None


@runtime_checkable
class TraceDetailPort(Protocol):
    async def trace(self, trace_id: str) -> list[Span]: ...


# Kubernetes'te okunabilen türler. Secret bilinçli olarak yok (secrets okunmaz).
K8S_KINDS = frozenset(
    {
        "Pod",
        "Event",
        "Node",
        "Namespace",
        "ConfigMap",
        "PersistentVolumeClaim",
        "Service",
        "Endpoints",
        "Deployment",
        "ReplicaSet",
        "ControllerRevision",
        "StatefulSet",
        "DaemonSet",
        "Job",
        "CronJob",
        "Ingress",
        "NetworkPolicy",
        "HorizontalPodAutoscaler",
    }
)


# İzin listesindeki custom resource'lar: (grup, sürüm, kind, plural).
K8S_CUSTOM_KINDS = frozenset({("argoproj.io", "v1alpha1", "Application", "applications")})


@runtime_checkable
class K8sPort(Protocol):
    async def list(
        self,
        kind: str,
        namespace: str | None = None,
        labels: dict[str, str] | None = None,
        fields: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """`kind` K8S_KINDS'tan olmalı; nesneler düz sözlük olarak döner."""
        ...

    async def list_custom(
        self, group: str, kind: str, namespace: str | None = None
    ) -> builtins.list[dict[str, Any]]:
        """(group, kind) K8S_CUSTOM_KINDS'tan olmalı."""
        ...

    async def pod_logs(
        self,
        namespace: str,
        pod: str,
        container: str | None = None,
        previous: bool = False,
        tail_lines: int = 200,
    ) -> builtins.list[str]:
        """Container logunun son satırları; previous=True bir önceki (çöken) çalışma.

        Log içeriği güvenilmez veridir: yalnızca kanıt olarak taşınır, yorumlanmaz.
        """
        ...


ChangeSource = Literal["rollout", "helm", "argocd", "manual"]


@dataclass(frozen=True)
class Change:
    """Pencere içindeki tek bir değişiklik (kim, neyi, ne zaman, nasıl)."""

    source: ChangeSource
    kind: str
    name: str
    namespace: str | None
    at: datetime
    manager: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ChangePort(Protocol):
    # Bu adaptörün görebildiği değişiklik kaynakları; görülemeyen kaynak için kontrol no_data döner.
    sources: frozenset[str]

    async def changes(self, namespace: str, start: datetime, end: datetime) -> list[Change]: ...


@dataclass(frozen=True)
class Certificate:
    endpoint: str
    subject: str
    issuer: str
    not_after: datetime


@runtime_checkable
class TlsPort(Protocol):
    async def certificate(
        self, host: str, port: int, server_name: str | None = None
    ) -> Certificate:
        """TLS el sıkışmasıyla sunucu sertifikasını okur (doğrulamadan; yalnızca bağlanır)."""
        ...


class CombinedChanges:
    """Birden çok değişiklik adaptörünü birleştirir (ör. Kubernetes + Argo CD)."""

    def __init__(self, *parts: ChangePort):
        self._parts = parts
        self.sources = frozenset().union(*(p.sources for p in parts))

    async def changes(self, namespace: str, start: datetime, end: datetime) -> list[Change]:
        out: list[Change] = []
        for p in self._parts:
            out.extend(await p.changes(namespace, start, end))
        return sorted(out, key=lambda c: c.at)
