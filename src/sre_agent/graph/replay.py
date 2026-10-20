"""MCP tool cevaplarının kaydı ve replay'i (ADR-034, demo mode).

Kayıt: canlı bir investigation'da her `list_tools` / `call_tool` cevabı incident raporlarıyla
birlikte JSON'a yazılır. Replay: specialist'ler aynı tool'ları görür, cevaplar kayıttan gelir;
model canlıdır, bu yüzden loop ve kararlar gerçektir. Kayıtta olmayan argümanla gelen çağrıya
aynı tool'un en yakın kaydı, bunu söyleyen bir notla döner.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def _text(res: Any) -> str:
    return "\n".join(getattr(c, "text", "") for c in res.content)


def _result(text: str, error: bool = False) -> Any:
    return SimpleNamespace(content=[SimpleNamespace(text=text)], is_error=error)


@dataclass
class Recorder:
    """Gerçek MCP istemcisini sarar; çağrıları ve cevapları biriktirir."""

    inner: Callable[[str], Any]  # sunucu adı → mcp.Client (async context manager)
    tools: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    @asynccontextmanager
    async def connect(self, server: str) -> AsyncIterator[Any]:
        async with self.inner(server) as session:
            yield _RecordingSession(self, server, session)

    def dump(self, path: Path, incident_id: str, reports: list[dict[str, Any]]) -> None:
        data = {
            "incident_id": incident_id,
            "recorded_at": datetime.now(UTC).isoformat(),
            "reports": reports,
            "tools": self.tools,
            "calls": self.calls,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str))


class _RecordingSession:
    def __init__(self, rec: Recorder, server: str, inner: Any):
        self.rec, self.server, self.inner = rec, server, inner

    async def list_tools(self) -> Any:
        listed = await self.inner.list_tools()
        self.rec.tools[self.server] = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in listed.tools
        ]
        return listed

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        entry = {"server": self.server, "tool": name, "arguments": dict(arguments or {})}
        try:
            res = await self.inner.call_tool(name, arguments)
        except Exception as exc:
            self.rec.calls.append(entry | {"text": f"{type(exc).__name__}: {exc}", "error": True})
            raise
        self.rec.calls.append(entry | {"text": _text(res), "error": bool(res.is_error)})
        return res


@dataclass
class Replay:
    """Kayıttan cevap veren MCP istemcisi."""

    data: dict[str, Any]
    misses: list[str] = field(default_factory=list)  # kayıtta birebir karşılığı olmayan çağrılar

    @classmethod
    def load(cls, path: Path) -> Replay:
        return cls(json.loads(path.read_text()))

    @property
    def servers(self) -> list[str]:
        return list(self.data.get("tools", {}))

    @asynccontextmanager
    async def connect(self, server: str) -> AsyncIterator[Any]:
        yield _ReplaySession(self, server)

    def answer(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        same = [c for c in self.data.get("calls", []) if (c["server"], c["tool"]) == (server, tool)]
        for c in same:
            if c["arguments"] == args:
                return _result(c["text"], c["error"])
        self.misses.append(f"{server}.{tool}({json.dumps(args, ensure_ascii=False)})")
        if not same:
            return _result(f"replay: {server}.{tool} için kayıt yok", error=True)
        # En çok argümanı tutan kayıt; eşitlikte ilki
        best = max(same, key=lambda c: sum(c["arguments"].get(k) == v for k, v in args.items()))
        note = (
            f"[replay: bu argümanlarla kayıt yok; en yakın kayıt "
            f"{json.dumps(best['arguments'], ensure_ascii=False)}]\n"
        )
        return _result(note + best["text"], best["error"])


class _ReplaySession:
    def __init__(self, replay: Replay, server: str):
        self.replay, self.server = replay, server

    async def list_tools(self) -> Any:
        tools = [SimpleNamespace(**t) for t in self.replay.data["tools"].get(self.server, [])]
        return SimpleNamespace(tools=tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return self.replay.answer(self.server, name, dict(arguments or {}))


class MemoryStore:
    """Investigator'ın kullandığı iki store işlemi, bellekte (demo ve kayıt dosyasından koşu)."""

    def __init__(self, reports: dict[str, list[dict[str, Any]]]):
        self.reports = reports
        self.investigations: dict[str, dict[str, Any]] = {}

    async def incident_reports(self, incident_id: str) -> list[dict[str, Any]]:
        return self.reports.get(incident_id, [])

    async def save_investigation(
        self, incident_id: str, result: dict[str, Any], mode: str, at: datetime
    ) -> None:
        self.investigations[incident_id] = result
