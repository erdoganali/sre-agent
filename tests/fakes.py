"""Birim testleri için sahte portlar."""

from __future__ import annotations

import builtins
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sre_agent.checks import CheckContext, Window
from sre_agent.ports import Capability, Certificate, LogLine, Sample, Series, TraceSummary

END = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
WINDOW = Window(start=END - timedelta(minutes=30), end=END)


class FakeMetrics:
    """Yanıtlar tam sorgu metnine ya da (resolver ile) sorgunun içeriğine göre seçilir."""

    def __init__(
        self,
        answers: dict[str, list[Sample]] | None = None,
        resolver: Callable[[str], list[Sample]] | None = None,
    ):
        self.answers = answers or {}
        self.resolver = resolver
        self.queries: list[str] = []

    async def query(self, promql: str, at: datetime) -> list[Sample]:
        self.queries.append(promql)
        if self.resolver:
            return self.resolver(promql)
        return self.answers.get(promql, [])

    async def query_range(
        self, promql: str, start: datetime, end: datetime, step_s: int
    ) -> list[Series]:
        return []


class FakeLogs:
    def __init__(self, resolver: Callable[[str, datetime, datetime], list[LogLine]] | None = None):
        self.resolver = resolver

    async def query_range(
        self, logql: str, start: datetime, end: datetime, limit: int
    ) -> list[LogLine]:
        return self.resolver(logql, start, end)[:limit] if self.resolver else []


class FakeTraces:
    def __init__(self, resolver: Callable[[str], list[TraceSummary]] | None = None):
        self.resolver = resolver

    async def search(
        self, traceql: str, start: datetime, end: datetime, limit: int
    ) -> list[TraceSummary]:
        return self.resolver(traceql)[:limit] if self.resolver else []


class FakeTls:
    def __init__(self, certs: dict[str, Certificate]):
        self.certs = certs

    async def certificate(
        self, host: str, port: int, server_name: str | None = None
    ) -> Certificate:
        key = f"{host}:{port}"
        if key not in self.certs:
            raise ConnectionRefusedError(key)
        return self.certs[key]


class FakeK8s:
    def __init__(
        self,
        objects: dict[str, list[dict[str, Any]]] | None = None,
        logs: dict[tuple[str, str, bool], list[str]] | None = None,
    ):
        self.objects = objects or {}
        self.logs = logs or {}  # (pod, container, previous) -> satırlar

    async def list(
        self,
        kind: str,
        namespace: str | None = None,
        labels: dict[str, str] | None = None,
        fields: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.objects.get(kind, [])

    async def list_custom(
        self, group: str, kind: str, namespace: str | None = None
    ) -> builtins.list[dict[str, Any]]:
        return self.objects.get(f"{group}/{kind}", [])

    async def pod_logs(
        self,
        namespace: str,
        pod: str,
        container: str | None = None,
        previous: bool = False,
        tail_lines: int = 200,
    ) -> builtins.list[str]:
        return self.logs.get((pod, container or "", previous), [])[-tail_lines:]


def ctx(labels: dict[str, str] | None = None, **ports: Any) -> CheckContext:
    return CheckContext(
        labels=labels or {}, window=WINDOW, ports={Capability(k): v for k, v in ports.items()}
    )
