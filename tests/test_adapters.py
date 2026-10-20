from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from sre_agent.adapters.kubernetes import KubernetesReader
from sre_agent.adapters.loki import LokiLogs
from sre_agent.adapters.prometheus import PrometheusError, PrometheusMetrics
from sre_agent.adapters.tempo import TempoTraces

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def client(body: Any, seen: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))


async def test_prometheus_query_parses_vector_and_only_gets() -> None:
    seen: list[httpx.Request] = []
    body = {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": {"a": "b"}, "value": [1, "4.5"]}]},
    }
    samples = await PrometheusMetrics("http://x", client=client(body, seen)).query("up", T0)
    assert samples[0].labels == {"a": "b"} and samples[0].value == 4.5
    assert seen[0].method == "GET" and seen[0].url.path == "/api/v1/query"


async def test_prometheus_error_status_raises() -> None:
    body = {"status": "error", "error": "parse error"}
    with pytest.raises(PrometheusError, match="parse error"):
        await PrometheusMetrics("http://x", client=client(body, [])).query("(", T0)


async def test_prometheus_query_range_parses_matrix() -> None:
    body = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {}, "values": [[1, "1"], [2, "2"]]}],
        },
    }
    series = await PrometheusMetrics("http://x", client=client(body, [])).query_range(
        "up", T0, T0, 60
    )
    assert [v for _, v in series[0].points] == [1.0, 2.0]


async def test_loki_parses_streams_newest_first() -> None:
    body = {
        "data": {
            "result": [
                {
                    "stream": {"job": "a"},
                    "values": [["1000000000", "eski"], ["3000000000", "yeni"]],
                },
                {"stream": {"job": "b"}, "values": [["2000000000", "orta"]]},
            ]
        }
    }
    seen: list[httpx.Request] = []
    lines = await LokiLogs("http://x", client=client(body, seen)).query_range("{}", T0, T0, 2)
    assert [entry.line for entry in lines] == ["yeni", "orta"]
    assert seen[0].method == "GET"


async def test_tempo_parses_search() -> None:
    body = {
        "traces": [
            {
                "traceID": "abc",
                "rootServiceName": "svc",
                "rootTraceName": "op",
                "startTimeUnixNano": "1790000000000000000",
                "durationMs": 12,
            }
        ]
    }
    traces = await TempoTraces("http://x", client=client(body, [])).search("{}", T0, T0, 5)
    assert traces[0].trace_id == "abc" and traces[0].duration_ms == 12


async def test_kubernetes_reader_refuses_secrets() -> None:
    reader = KubernetesReader(client=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="okunamayan tür"):
        await reader.list("Secret", namespace="x")
