"""Token bütçesi (ADR-004): uygulama katmanında zorlanır, sunucu ayarlarına güvenilmez.

- Koşu: yumuşak 600K (geçilince `wrap_up`: erken sentez), sert 1M (çağrı reddedilir).
- Uzman: kümülatif 120K (red; uzman tool'suz son cevaba geçer, Faz 3).
- Tek tool sonucu: ~8K (kırpma + "kırpıldı" satırı).
- Eşzamanlılık: 2 koşu, öncelik kuyruğu (critical önce, sonra geliş sırası).

Çağrıdan önce tahmini token ayrılır (bağlam + max_tokens); sınırı aşacaksa çağrı yapılmaz.
Çağrıdan sonra sunucunun döndürdüğü gerçek `usage` ile düzeltilir.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from sre_agent.llm.catalog import Catalog
from sre_agent.ports.model import (
    Classification,
    Completion,
    Message,
    ModelPort,
    T,
    ToolSpec,
    ToolTurn,
    Typed,
    Usage,
)

CHARS_PER_TOKEN = 3.5  # tokenizer yokken kaba tahmin (Türkçe/İngilizce karışık metin, kod)
MESSAGE_OVERHEAD = 4  # mesaj başına şablon token'ları


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_soft: int = 600_000
    run_hard: int = 1_000_000
    specialist: int = 120_000
    tool_result: int = 8_000


class BudgetExceeded(Exception):
    def __init__(self, scope: str, used: int, needed: int, limit: int):
        super().__init__(f"{scope} bütçesi: kullanılan {used} + istenen {needed} > sınır {limit}")
        self.scope = scope


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def estimate_messages(messages: list[Message], count: Callable[[str], int]) -> int:
    return sum(count(m.content) + MESSAGE_OVERHEAD for m in messages)


def truncate_tool_result(
    text: str, limit: int = Limits().tool_result, count: Callable[[str], int] = estimate_tokens
) -> tuple[str, bool]:
    """Tek tool sonucunu ~limit token'a indirir; kırpıldıysa sona özet satırı ekler."""
    total = count(text)
    if total <= limit:
        return text, False
    note_budget = 32
    keep = int(len(text) * (limit - note_budget) / total)
    cut = text.rfind("\n", 0, keep)
    head = text[: cut if cut > keep // 2 else keep]
    dropped = total - count(head)
    return (
        f"{head}\n… [kırpıldı: ~{dropped} token, toplam ~{total}; ayrıntı için sorguyu daralt]",
        True,
    )


@dataclass
class Budget:
    """Bir koşunun token hesabı. Uzmanlar `scope` adıyla ayrı sayılır."""

    limits: Limits = field(default_factory=Limits)
    count: Callable[[str], int] = estimate_tokens
    used: int = 0
    by_scope: dict[str, int] = field(default_factory=dict)
    reserved: int = 0
    truncated_tool_results: int = 0
    rejected: int = 0

    @property
    def wrap_up(self) -> bool:
        """Yumuşak sınır geçildi: graf erken sentez yapmalı."""
        return self.used >= self.limits.run_soft

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limits.run_hard

    def reserve(self, needed: int, scope: str | None) -> None:
        if self.used + self.reserved + needed > self.limits.run_hard:
            self.rejected += 1
            raise BudgetExceeded("koşu", self.used + self.reserved, needed, self.limits.run_hard)
        if scope is not None:
            mine = self.by_scope.get(scope, 0)
            if mine + needed > self.limits.specialist:
                self.rejected += 1
                raise BudgetExceeded(f"uzman:{scope}", mine, needed, self.limits.specialist)
        self.reserved += needed

    def settle(self, reserved: int, usage: Usage, scope: str | None) -> None:
        self.reserved -= reserved
        spent = usage.total or reserved  # sunucu usage döndürmezse tahmin geçerli
        self.used += spent
        if scope is not None:
            self.by_scope[scope] = self.by_scope.get(scope, 0) + spent

    def release(self, reserved: int) -> None:
        self.reserved -= reserved

    def tool_result(self, text: str) -> str:
        out, cut = truncate_tool_result(text, self.limits.tool_result, self.count)
        self.truncated_tool_results += cut
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "used": self.used,
            "by_scope": dict(self.by_scope),
            "wrap_up": self.wrap_up,
            "exhausted": self.exhausted,
            "rejected": self.rejected,
            "truncated_tool_results": self.truncated_tool_results,
        }


class BudgetedModel:
    """Model portunu bütçeyle sarar; adaptör değişmez. `scope`: uzman adı (koşu geneli: None)."""

    def __init__(
        self, inner: ModelPort, catalog: Catalog, budget: Budget, scope: str | None = None
    ):
        self.inner = inner
        self.catalog = catalog
        self.budget = budget
        self.scope = scope

    def for_scope(self, scope: str) -> BudgetedModel:
        return BudgetedModel(self.inner, self.catalog, self.budget, scope)

    def _need(self, role: str, messages: list[Message], max_tokens: int | None) -> int:
        _, spec, r = self.catalog.resolve(role)
        prompt = estimate_messages(messages, self.budget.count)
        out = max_tokens or r.max_tokens or int(spec.sampling_params.get("max_tokens", 0)) or 1024
        if prompt + out > spec.max_context_tokens:
            self.budget.rejected += 1
            raise BudgetExceeded("bağlam", prompt, out, spec.max_context_tokens)
        return prompt + out

    @asynccontextmanager
    async def _spend(self, need: int) -> AsyncIterator[list[Usage]]:
        self.budget.reserve(need, self.scope)
        box: list[Usage] = []
        try:
            yield box
        except BaseException:
            self.budget.release(need)
            raise
        self.budget.settle(need, box[0] if box else Usage(), self.scope)

    async def complete(
        self,
        role: str,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Completion:
        async with self._spend(self._need(role, messages, max_tokens)) as box:
            c = await self.inner.complete(
                role, messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort
            )
            box.append(c.usage)
            return c

    async def complete_typed(
        self,
        role: str,
        messages: list[Message],
        schema: type[T],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Typed[T]:
        # Yedek yol bir düzeltme turu daha yapabilir: iki çağrılık yer ayrılır.
        async with self._spend(2 * self._need(role, messages, max_tokens)) as box:
            t = await self.inner.complete_typed(
                role, messages, schema, max_tokens=max_tokens, reasoning_effort=reasoning_effort
            )
            box.append(t.completion.usage)
            return t

    async def complete_tools(
        self,
        role: str,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int | None = None,
    ) -> ToolTurn:
        # Araç şemaları da isteğe girer; JSON yedeği bir düzeltme turu yapabilir: iki çağrılık yer
        schemas = sum(self.budget.count(f"{t.name} {t.description} {t.parameters}") for t in tools)
        need = 2 * (self._need(role, messages, max_tokens) + schemas)
        inner: Any = self.inner
        async with self._spend(need) as box:
            turn: ToolTurn = await inner.complete_tools(
                role, messages, tools, max_tokens=max_tokens
            )
            box.append(turn.completion.usage)
            return turn

    async def classify(
        self, role: str, messages: list[Message], labels: list[str]
    ) -> Classification:
        async with self._spend(2 * self._need(role, messages, 512)) as box:
            c = await self.inner.classify(role, messages, labels)
            box.append(c.completion.usage)
            return c


class PriorityGate:
    """Aynı anda en fazla `limit` koşu; bekleyenler önceliğe (küçük önce), sonra gelişe göre."""

    def __init__(self, limit: int, on_change: Callable[[int], None] | None = None):
        self.limit = limit
        self.active = 0
        self._on_change = on_change or (lambda _: None)  # bekleyen sayısı (metrik için)
        self._queue: list[tuple[int, int, asyncio.Future[None]]] = []
        self._seq = itertools.count()

    @property
    def waiting(self) -> int:
        return sum(1 for *_, f in self._queue if not f.done())

    @asynccontextmanager
    async def slot(self, priority: int = 1) -> AsyncIterator[None]:
        if self.active < self.limit and not self.waiting:
            self.active += 1
        else:
            fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            heapq.heappush(self._queue, (priority, next(self._seq), fut))
            self._on_change(self.waiting)
            try:
                await fut  # _release slotu bize devreder (active zaten sayılmış olur)
            except asyncio.CancelledError:
                if fut.done() and not fut.cancelled():
                    self._release()  # slot verilmişti ama kullanılmayacak
                self._on_change(self.waiting)
                raise
            self._on_change(self.waiting)
        try:
            yield
        finally:
            self._release()

    def _release(self) -> None:
        while self._queue:
            *_, fut = heapq.heappop(self._queue)
            if not fut.done():
                fut.set_result(None)  # slot devredildi; active değişmez
                return
        self.active -= 1


SEVERITY_PRIORITY = {"critical": 0, "warning": 1}


def alert_priority(alert: dict[str, Any]) -> int:
    sev = str((alert.get("labels") or {}).get("severity", "")).lower()
    return SEVERITY_PRIORITY.get(sev, 2)
