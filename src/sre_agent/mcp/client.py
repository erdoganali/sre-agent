"""Specialist'in araç istemcisi: tekrar eden çağrı kısa devre, kırpma, güvenilmez veri işareti.

Bir ToolClient bir specialist koşusuna aittir (T3.4): tekrar yakalama koşu içinde yapılır, aynı
anda koşan başka incident'ların çağrılarıyla karışmaz. Her çağrı `calls`'a yazılır; bulgunun
kanıtı (araç çağrısı + dönen veri) buradan gelir.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from sre_agent.llm.budget import Budget


class ToolSession(Protocol):
    """mcp.Client'ın kullandığımız kısmı (testte sahtesi)."""

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...


@dataclass
class ToolCall:
    server: str
    tool: str
    arguments: dict[str, Any]
    result: str
    error: bool = False
    repeated: bool = False


@dataclass
class ToolClient:
    sessions: Mapping[str, ToolSession]  # sunucu adı → bağlı istemci
    budget: Budget = field(default_factory=Budget)
    calls: list[ToolCall] = field(default_factory=list)
    _seen: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def key(server: str, tool: str, arguments: Mapping[str, Any]) -> str:
        return json.dumps([server, tool, dict(arguments)], sort_keys=True, ensure_ascii=False)

    async def call(self, server: str, tool: str, arguments: dict[str, Any] | None = None) -> str:
        args = {k: v for k, v in (arguments or {}).items() if v is not None}
        k = self.key(server, tool, args)
        if k in self._seen:  # aynı argümanla tekrar: sunucuya gidilmez, model önceki cevabına bakar
            first = self._seen[k]
            note = (
                f"Bu çağrı ({server}.{tool}, aynı argümanlar) bu koşuda {first + 1}. çağrıda "
                "yapıldı; sonuç orada. Farklı bir pencere ya da sorgu dene."
            )
            self.calls.append(ToolCall(server, tool, args, note, repeated=True))
            return note
        session = self.sessions.get(server)
        if session is None:
            text, error = f"{server} sunucusu yok ya da erişilemiyor", True
        else:
            try:
                res = await session.call_tool(tool, args)
                text = "\n".join(getattr(c, "text", "") for c in res.content)
                error = bool(res.is_error)
            except Exception as exc:  # ağ, zaman aşımı: specialist "veri yok" bulgusu döner
                text, error = f"{server}.{tool} çağrılamadı: {type(exc).__name__}: {exc}", True
        text = self.budget.tool_result(text)
        self._seen[k] = len(self.calls)
        self.calls.append(ToolCall(server, tool, args, text, error))
        tag = "araç_hatası" if error else "guvenilmez_veri"
        return f'<{tag} kaynak="{server}.{tool}">\n{text}\n</{tag}>'
