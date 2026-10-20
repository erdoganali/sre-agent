"""traces sunucusu: Tempo."""

from __future__ import annotations

import re

from mcp.server.mcpserver import MCPServer

from sre_agent.mcp.common import READ_ONLY, ToolInputError, cap, guarded, num, table, window
from sre_agent.mcp.common import ts as fmt_ts
from sre_agent.mcp.explain import why_empty_traces
from sre_agent.ports import TraceDetailPort, TracesPort

MAX_MINUTES = 24 * 60
MAX_TRACES = 50
_TRACE_ID = re.compile(r"^[0-9a-fA-F]{16,32}$")


def build(port: TracesPort) -> MCPServer:
    s = MCPServer(
        "traces",
        instructions="Tempo trace'leri (salt okunur). Önce search (TraceQL), sonra trace_summary.",
    )

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def search(
        traceql: str, minutes: int = 60, end: str | None = None, limit: int = 20
    ) -> str:
        """TraceQL araması, ör. '{ resource.service.name = "x" && status = error }'. Süreye göre
        azalan; limit ≤ 50."""
        if not 1 <= limit <= MAX_TRACES:
            raise ToolInputError(f"limit 1..{MAX_TRACES} olmalı")
        start, stop = window(end, minutes, MAX_MINUTES)
        found = await port.search(traceql, start, stop, limit)
        if not found:
            why = await why_empty_traces(port, start, stop)
            return f"`{traceql}` {fmt_ts(start)} → {fmt_ts(stop)}: trace yok.\n{why}"
        found.sort(key=lambda t: -(t.duration_ms or 0))
        rows = [
            [
                t.trace_id,
                fmt_ts(t.start),
                num(t.duration_ms),
                t.root_service or "-",
                t.root_name or "-",
            ]
            for t in found
        ]
        body = table(["trace", "başlangıç", "süre ms", "kök servis", "kök span"], rows)
        return cap(f"`{traceql}` — {len(found)} trace\n\n{body}")

    if isinstance(port, TraceDetailPort):
        detail: TraceDetailPort = port

        @s.tool(annotations=READ_ONLY)
        @guarded
        async def trace_summary(trace_id: str) -> str:
            """Bir trace'in özeti: servisler, hatalı span'lar (mesajıyla), en uzun 10 span."""
            if not _TRACE_ID.match(trace_id):
                raise ToolInputError("trace_id 16-32 haneli hex olmalı")
            spans = await detail.trace(trace_id)
            if not spans:
                return f"{trace_id}: trace bulunamadı"
            services = sorted({sp.service or "?" for sp in spans})
            root = next((sp for sp in spans if not sp.parent_id), spans[0])
            errors = [sp for sp in spans if sp.error]
            parts = [
                f"trace {trace_id}: {len(spans)} span, {len(errors)} hatalı; servisler: "
                f"{', '.join(services)}; kök: {root.service} / {root.name} "
                f"({num(root.duration_ms)} ms, {fmt_ts(root.start)})"
            ]
            if errors:
                parts.append(
                    "Hatalı span'lar:\n\n"
                    + table(
                        ["servis", "span", "süre ms", "mesaj"],
                        [
                            [e.service, e.name, num(e.duration_ms), e.status_message or "-"]
                            for e in errors
                        ],
                        max_rows=20,
                    )
                )
            slow = sorted(spans, key=lambda sp: -sp.duration_ms)[:10]
            parts.append(
                "En uzun span'lar:\n\n"
                + table(
                    ["servis", "span", "süre ms", "hata"],
                    [
                        [sp.service, sp.name, num(sp.duration_ms), "evet" if sp.error else ""]
                        for sp in slow
                    ],
                )
            )
            return cap("\n\n".join(parts))

    return s
