from __future__ import annotations

from datetime import UTC, datetime

from sre_agent.adapters._http import HttpReader
from sre_agent.ports import Span, TraceSummary


class TempoTraces(HttpReader):
    """TracesPort: Tempo arama API'si (/api/search, TraceQL)."""

    async def search(
        self, traceql: str, start: datetime, end: datetime, limit: int
    ) -> list[TraceSummary]:
        body = await self.get_json(
            "/api/search",
            {
                "q": traceql,
                "start": int(start.timestamp()),
                "end": int(end.timestamp()),
                "limit": limit,
            },
        )
        out: list[TraceSummary] = []
        for t in body.get("traces") or []:
            start_ns = t.get("startTimeUnixNano")
            out.append(
                TraceSummary(
                    trace_id=t["traceID"],
                    root_service=t.get("rootServiceName"),
                    root_name=t.get("rootTraceName"),
                    start=datetime.fromtimestamp(int(start_ns) / 1e9, UTC) if start_ns else None,
                    duration_ms=t.get("durationMs"),
                )
            )
        return out

    async def trace(self, trace_id: str) -> list[Span]:
        """TraceDetailPort: /api/traces/<id> (OTLP JSON; eski 'batches' ve yeni 'trace' biçimi)."""
        body = await self.get_json(f"/api/traces/{trace_id}", {})
        groups = body.get("batches") or (body.get("trace") or {}).get("resourceSpans") or []
        spans: list[Span] = []
        for rs in groups:
            attrs = (rs.get("resource") or {}).get("attributes") or []
            service = next(
                (
                    a.get("value", {}).get("stringValue")
                    for a in attrs
                    if a.get("key") == "service.name"
                ),
                None,
            )
            for ss in rs.get("scopeSpans") or rs.get("instrumentationLibrarySpans") or []:
                for sp in ss.get("spans") or []:
                    start = int(sp.get("startTimeUnixNano") or 0)
                    end = int(sp.get("endTimeUnixNano") or start)
                    status = sp.get("status") or {}
                    code = status.get("code")
                    spans.append(
                        Span(
                            span_id=str(sp.get("spanId", "")),
                            parent_id=sp.get("parentSpanId") or None,
                            service=service,
                            name=str(sp.get("name", "")),
                            start=datetime.fromtimestamp(start / 1e9, UTC),
                            duration_ms=(end - start) / 1e6,
                            error=code in (2, "STATUS_CODE_ERROR"),
                            status_message=status.get("message") or None,
                        )
                    )
        return spans
