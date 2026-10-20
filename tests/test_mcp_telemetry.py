"""MCP telemetri sunucuları (T3.3): araç listesi ve şeması, sorgu biçimi, sınırlar, istemci."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import Client

from sre_agent.llm.budget import Budget, Limits
from sre_agent.mcp import logs, metrics, traces
from sre_agent.mcp.client import ToolClient
from sre_agent.ports import LogLine, Sample, Series, Span, TraceSummary

T0 = datetime(2026, 10, 9, 16, 56, tzinfo=UTC)


class FakeMetrics:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def query(self, promql: str, at: datetime) -> list[Sample]:
        self.queries.append(promql)
        if "restarts_total" in promql:
            return [Sample({"pod": "cus-acme-etcd-0"}, 4.0), Sample({"pod": "proxy-1"}, 0.0)]
        if "last_terminated_reason" in promql:
            return [Sample({"pod": "cus-acme-etcd-0", "reason": "OOMKilled"}, 1.0)]
        if "count by" in promql:
            return [Sample({"__name__": "kube_pod_info"}, 120.0)]
        if promql == "up":
            return [Sample({"job": "a"}, 1.0), Sample({"job": "b"}, 0.0)]
        return []

    async def query_range(
        self, promql: str, start: datetime, end: datetime, step_s: int
    ) -> list[Series]:
        self.queries.append(f"range:{promql}:{step_s}")
        pts = [(start + timedelta(minutes=i), float(v)) for i, v in enumerate([1, 5, 2])]
        return [Series({"pod": "p"}, pts)]


class FakeLogs:
    def __init__(self, lines: list[LogLine]) -> None:
        self.lines = lines
        self.queries: list[str] = []

    async def query_range(
        self, logql: str, start: datetime, end: datetime, limit: int
    ) -> list[LogLine]:
        self.queries.append(logql)
        return self.lines[:limit]


class FakeTraces:
    async def search(
        self, traceql: str, start: datetime, end: datetime, limit: int
    ) -> list[TraceSummary]:
        return [
            TraceSummary("a" * 32, "backend", "GET /x", T0, 120.0),
            TraceSummary("b" * 32, "backend", "GET /y", T0, 900.0),
        ]

    async def trace(self, trace_id: str) -> list[Span]:
        return [
            Span("1", None, "backend", "GET /y", T0, 900.0, False),
            Span("2", "1", "milvus-proxy", "Search", T0, 850.0, True, "deadline exceeded"),
        ]


async def tools(server: Any) -> dict[str, Any]:
    async with Client(server) as c:
        return {t.name: t for t in (await c.list_tools()).tools}


async def call(server: Any, name: str, **args: Any) -> tuple[bool, str]:
    async with Client(server) as c:
        r = await c.call_tool(name, args)
        return bool(r.is_error), "\n".join(getattr(x, "text", "") for x in r.content)


# ---- araç listesi ve şeması (sözleşme: hedef ortamda adaptör değişir, bunlar değişmez)

EXPECTED = {
    "metrics": {
        "query": ["promql"],
        "range_summary": ["promql"],
        "discover": ["name_regex"],
        "workload_signals": ["namespace"],
    },
    "logs": {"query": ["logql"], "error_patterns": ["selector"]},
    "traces": {"search": ["traceql"], "trace_summary": ["trace_id"]},
}


@pytest.mark.parametrize("name", list(EXPECTED))
async def test_tool_list_schema_and_read_only(name: str) -> None:
    server = {
        "metrics": metrics.build(FakeMetrics()),
        "logs": logs.build(FakeLogs([])),
        "traces": traces.build(FakeTraces()),
    }[name]
    got = await tools(server)
    assert {n: t.input_schema.get("required", []) for n, t in got.items()} == EXPECTED[name]
    for t in got.values():
        assert t.description and t.annotations and t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False


async def test_trace_summary_only_when_port_supports_detail() -> None:
    class SearchOnly:
        search = FakeTraces.search

    assert set(await tools(traces.build(SearchOnly()))) == {"search"}


# ---- metrics


async def test_query_sorts_by_value_and_renders_markdown() -> None:
    err, text = await call(metrics.build(FakeMetrics()), "query", promql="up")
    assert not err and "| etiketler | değer |" in text
    assert text.index("job=a") < text.index("job=b")


async def test_range_summary_gives_peak_not_raw_points() -> None:
    fake = FakeMetrics()
    err, text = await call(
        metrics.build(fake), "range_summary", promql="x", minutes=120, end="2026-10-09T17:00:00Z"
    )
    assert not err and "| pod=p | 1 | 2.67 | 5 | 2026-10-09 15:01:00Z | 1 | 2 |" in text
    assert fake.queries == ["range:x:60"]  # 120 dk → ~120 nokta


async def test_workload_signals_is_one_call_many_queries() -> None:
    fake = FakeMetrics()
    err, text = await call(metrics.build(fake), "workload_signals", namespace="acme")
    assert not err and len(fake.queries) == 7
    assert all('namespace="acme"' in q for q in fake.queries)
    row = next(line for line in text.splitlines() if line.startswith("| cus-acme-etcd-0"))
    assert "| 4 | OOMKilled |" in row
    assert "Öne çıkanlar:\n- cus-acme-etcd-0: 4 restart; son sonlanma: OOMKilled" in text
    assert "proxy-1" not in text.split("| pod |")[0]  # sorunsuz pod öne çıkmaz


@pytest.mark.parametrize(
    ("args", "msg"),
    [
        ({"namespace": "acme", "pod_regex": 'x"} or vector(1) #'}, "tırnak"),
        ({"namespace": "Acme!"}, "Kubernetes adı"),
        ({"namespace": "acme", "minutes": 99999}, "minutes"),
        ({"namespace": "acme", "end": "dün"}, "ISO 8601"),
    ],
)
async def test_bad_arguments_are_rejected_without_querying(args: dict[str, Any], msg: str) -> None:
    fake = FakeMetrics()
    err, text = await call(metrics.build(fake), "workload_signals", **args)
    assert err and msg in text and fake.queries == []


# ---- logs


def line(pod: str, text: str, minute: int) -> LogLine:
    return LogLine({"pod": pod, "service_name": "milvus"}, T0 + timedelta(minutes=minute), text)


async def test_error_patterns_groups_by_signature() -> None:
    fake = FakeLogs(
        [
            line("etcd-0", "error: etcdclient: no available endpoints id=123", 1),
            line("etcd-1", "error: etcdclient: no available endpoints id=456", 2),
            line("proxy", "panic: nil pointer", 3),
        ]
    )
    err, text = await call(logs.build(fake), "error_patterns", selector='{service_name="milvus"}')
    assert not err and "2 hata imzası, 3 satır tarandı" in text
    row = next(r for r in text.splitlines() if r.startswith("|") and "no available endpoints" in r)
    assert row.startswith("| 2 |") and "etcd-0, etcd-1" in row
    assert "Öne çıkanlar:\n- 2 kez" in text
    assert fake.queries[0].startswith('{service_name="milvus"} |~')


async def test_error_patterns_requires_stream_selector() -> None:
    err, text = await call(logs.build(FakeLogs([])), "error_patterns", selector="rate(x[5m])")
    assert err and "akış seçicisi" in text


# ---- traces


async def test_trace_summary_lists_error_spans() -> None:
    err, text = await call(traces.build(FakeTraces()), "trace_summary", trace_id="b" * 32)
    assert not err and "2 span, 1 hatalı" in text and "deadline exceeded" in text


async def test_search_sorts_slowest_first() -> None:
    err, text = await call(traces.build(FakeTraces()), "search", traceql="{ status = error }")
    assert not err and text.index("b" * 32) < text.index("a" * 32)


# ---- istemci: tekrar, kırpma, işaret


class FakeSession:
    def __init__(self, text: str, error: bool = False) -> None:
        self.text, self.error, self.calls = text, error, 0

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(text=self.text)], is_error=self.error)


async def test_client_short_circuits_repeated_call() -> None:
    s = FakeSession("sonuç")
    c = ToolClient({"metrics": s})
    first = await c.call("metrics", "query", {"promql": "up", "at": None})
    again = await c.call("metrics", "query", {"promql": "up"})  # None argüman yok sayılır
    assert s.calls == 1 and '<guvenilmez_veri kaynak="metrics.query">' in first
    assert "1. çağrıda" in again and c.calls[1].repeated


async def test_client_truncates_with_budget_and_marks_errors() -> None:
    b = Budget(limits=Limits(tool_result=50))
    c = ToolClient({"logs": FakeSession("x" * 5000)}, budget=b)
    out = await c.call("logs", "query", {"logql": "{a=1}"})
    assert len(out) < 1000 and b.truncated_tool_results == 1
    missing = await c.call("traces", "search", {"traceql": "{}"})
    assert missing.startswith("<araç_hatası") and c.calls[-1].error


async def test_backend_error_message_reaches_the_model() -> None:
    from sre_agent.adapters.prometheus import PrometheusError

    class Broken(FakeMetrics):
        async def query(self, promql: str, at: datetime) -> list[Sample]:
            raise PrometheusError('parse error: unexpected "}"')

    err, text = await call(metrics.build(Broken()), "query", promql="up{")
    assert err and "parse error" in text


def test_error_filter_ignores_error_code_fields() -> None:
    import re

    inner = logs.ERROR_FILTER.split("`")[1]
    rx = re.compile(inner)
    assert not rx.search('{"status":"Successful","error_code":"0"}')
    assert rx.search('{"status":"Failed","error_code":"700"}')
    assert rx.search("level=error msg=etcdclient: no available endpoints")


# ---- boş sonucun sebebi (T3.5)


class EmptyAt(FakeMetrics):
    """`up` yok: o anda hiç veri yok (saklama süresi dışı)."""

    def __init__(self, has_data: bool, namespaces: list[str] | None = None) -> None:
        super().__init__()
        self.has_data, self.namespaces = has_data, namespaces or []

    async def query(self, promql: str, at: datetime) -> list[Sample]:
        self.queries.append(promql)
        if promql == "count(up)":
            return [Sample({}, 42.0)] if self.has_data else []
        if promql.startswith("count by (namespace)"):
            return [Sample({"namespace": n}, 1.0) for n in self.namespaces]
        return []


async def test_empty_metrics_says_data_is_outside_retention() -> None:
    err, text = await call(
        metrics.build(EmptyAt(False)), "query", promql="up", at="2026-10-01T09:00:00Z"
    )
    assert not err and "hiç veri yok" in text and "sorun yok" in text


async def test_empty_workload_lists_existing_namespaces() -> None:
    srv = metrics.build(EmptyAt(True, ["acme", "pilot"]))
    err, text = await call(srv, "workload_signals", namespace="laodtest")
    assert not err and "'laodtest' namespace'inde" in text and "acme, pilot" in text


async def test_empty_query_with_data_points_to_discover() -> None:
    err, text = await call(metrics.build(EmptyAt(True)), "query", promql="nope")
    assert not err and "sorgu eşleşmedi" in text and "discover" in text


async def test_empty_logs_window_is_explained() -> None:
    err, text = await call(
        logs.build(FakeLogs([])), "error_patterns", selector='{service_name="x"}'
    )
    assert not err and "hiç log yok" in text
