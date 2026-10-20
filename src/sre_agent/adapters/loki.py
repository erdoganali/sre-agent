from __future__ import annotations

from datetime import UTC, datetime

from sre_agent.adapters._http import HttpReader
from sre_agent.ports import LogLine


def _ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


class LokiLogs(HttpReader):
    """LogsPort: Loki HTTP API (/loki/api/v1/query_range). Log içeriği güvenilmez veridir."""

    async def query_range(
        self, logql: str, start: datetime, end: datetime, limit: int
    ) -> list[LogLine]:
        body = await self.get_json(
            "/loki/api/v1/query_range",
            {
                "query": logql,
                "start": _ns(start),
                "end": _ns(end),
                "limit": limit,
                "direction": "backward",
            },
        )
        lines: list[LogLine] = []
        for stream in body["data"]["result"]:
            labels = stream.get("stream", {})
            for ts, line, *_ in stream.get("values", []):
                lines.append(
                    LogLine(
                        labels=labels,
                        ts=datetime.fromtimestamp(int(ts) / 1_000_000_000, UTC),
                        line=line,
                    )
                )
        lines.sort(key=lambda entry: entry.ts, reverse=True)
        return lines[:limit]
