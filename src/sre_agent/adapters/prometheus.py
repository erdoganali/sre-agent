from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sre_agent.adapters._http import HttpReader
from sre_agent.ports import Sample, Series


class PrometheusError(RuntimeError):
    pass


class PrometheusMetrics(HttpReader):
    """MetricsPort: Prometheus HTTP API (/api/v1/query, /api/v1/query_range)."""

    async def _data(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        body = await self.get_json(path, params)
        if body.get("status") != "success":
            raise PrometheusError(body.get("error", "bilinmeyen hata"))
        data = body["data"]
        if data.get("resultType") not in ("vector", "matrix"):
            raise PrometheusError(f"beklenmeyen sonuç tipi {data.get('resultType')}")
        result: list[dict[str, Any]] = data["result"]
        return result

    async def query(self, promql: str, at: datetime) -> list[Sample]:
        rows = await self._data("/api/v1/query", {"query": promql, "time": at.timestamp()})
        return [Sample(labels=r["metric"], value=float(r["value"][1])) for r in rows]

    async def query_range(
        self, promql: str, start: datetime, end: datetime, step_s: int
    ) -> list[Series]:
        rows = await self._data(
            "/api/v1/query_range",
            {"query": promql, "start": start.timestamp(), "end": end.timestamp(), "step": step_s},
        )
        return [
            Series(
                labels=r["metric"],
                points=[(datetime.fromtimestamp(float(t), UTC), float(v)) for t, v in r["values"]],
            )
            for r in rows
        ]
