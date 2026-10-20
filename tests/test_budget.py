"""Token bütçesi ve öncelik kuyruğu (T2.2, ADR-004)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from sre_agent.llm.budget import (
    Budget,
    BudgetedModel,
    BudgetExceeded,
    Limits,
    PriorityGate,
    alert_priority,
    truncate_tool_result,
)
from sre_agent.llm.catalog import Catalog
from sre_agent.ports.model import Classification, Completion, Message, Typed, Usage

CAT = Catalog.model_validate(
    {
        "models": {"m": {"name": "m", "url": "http://x", "max_context_tokens": 10_000}},
        "roles": {"r": {"model": "m", "max_tokens": 100}},
    }
)


def one_char(text: str) -> int:  # testlerde 1 karakter = 1 token
    return len(text)


class Fake:
    """Sayılan çağrılar; sabit usage döndürür."""

    def __init__(self, usage: Usage | None = None):
        self.calls = 0
        self.usage = usage or Usage(50, 20)

    def _c(self) -> Completion:
        self.calls += 1
        return Completion(text="ok", model="m", usage=self.usage, latency_s=0)

    async def complete(self, role: str, messages: list[Message], **_: Any) -> Completion:
        return self._c()

    async def complete_typed(
        self, role: str, messages: list[Message], schema: Any, **_: Any
    ) -> Any:
        return Typed(value=schema(), completion=self._c())

    async def classify(
        self, role: str, messages: list[Message], labels: list[str]
    ) -> Classification:
        return Classification(probs={labels[0]: 1.0}, method="typed", completion=self._c())


def model(limits: Limits, fake: Fake | None = None) -> tuple[BudgetedModel, Budget, Fake]:
    budget = Budget(limits=limits, count=one_char)
    fake = fake or Fake()
    return BudgetedModel(fake, CAT, budget), budget, fake


MSG = [Message("user", "x" * 96)]  # 96 + 4 şablon = 100 token; + max_tokens 100 = 200 ayrılır


async def test_settles_with_real_usage_and_counts_scope() -> None:
    m, b, _ = model(Limits())
    await m.complete("r", MSG)
    await m.for_scope("metrik").complete("r", MSG)
    assert b.used == 140 and b.by_scope == {"metrik": 70} and b.reserved == 0


async def test_hard_limit_blocks_call_before_it_is_made() -> None:
    m, b, fake = model(Limits(run_soft=100, run_hard=399))
    for _ in range(3):  # her çağrı 200 ayırır, 70 harcar: 0, 70, 140 → hepsi sığar
        await m.complete("r", MSG)
    assert b.used == 210 and b.wrap_up
    with pytest.raises(BudgetExceeded, match="koşu"):
        await m.complete("r", MSG)  # 210 + 200 > 399
    assert fake.calls == 3 and b.rejected == 1 and b.used == 210


async def test_soft_limit_signals_wrap_up() -> None:
    m, b, _ = model(Limits(run_soft=70))
    await m.complete("r", MSG)
    assert b.wrap_up and not b.exhausted


async def test_specialist_limit_is_per_scope() -> None:
    m, b, fake = model(Limits(specialist=250))
    log = m.for_scope("log")
    await log.complete("r", MSG)  # log: 70
    with pytest.raises(BudgetExceeded, match="uzman:log"):
        await log.complete("r", MSG)  # 70 + 200 > 250
    await m.for_scope("metrik").complete("r", MSG)  # başka uzman etkilenmez
    assert fake.calls == 2 and b.by_scope == {"log": 70, "metrik": 70}


async def test_context_window_checked() -> None:
    m, _b, fake = model(Limits())
    with pytest.raises(BudgetExceeded, match="bağlam"):
        await m.complete("r", [Message("user", "x" * 9_950)])
    assert fake.calls == 0


async def test_failed_call_releases_reservation() -> None:
    class Boom(Fake):
        async def complete(self, role: str, messages: list[Message], **_: Any) -> Completion:
            raise RuntimeError("model düştü")

    m, b, _ = model(Limits(), Boom())
    with pytest.raises(RuntimeError):
        await m.complete("r", MSG)
    assert b.reserved == 0 and b.used == 0


async def test_missing_usage_counts_estimate() -> None:
    m, b, _ = model(Limits(), Fake(Usage(0, 0)))
    await m.complete("r", MSG)
    assert b.used == 200


async def test_typed_and_classify_reserve_room_for_repair() -> None:
    class V(BaseModel):
        a: int = 1

    m, _b, _ = model(Limits(run_hard=399))
    with pytest.raises(BudgetExceeded):
        await m.complete_typed("r", MSG, V)  # 2 x 200 > 399
    m2, b2, _ = model(Limits(run_hard=400))
    await m2.complete_typed("r", MSG, V)
    assert b2.used == 70
    m3, _b3, _ = model(Limits(run_hard=1215))  # sınıflama 2 x (100 + 512) = 1224 ayırır
    with pytest.raises(BudgetExceeded):
        await m3.classify("r", MSG, ["a", "b"])


def test_tool_result_truncated_with_note() -> None:
    text = "\n".join(f"satır {i:04d}" for i in range(2000))
    out, cut = truncate_tool_result(text, limit=500, count=one_char)
    assert cut and len(out) <= 500 + 120
    assert out.splitlines()[-1].startswith("… [kırpıldı:")
    assert truncate_tool_result("kısa", limit=500, count=one_char) == ("kısa", False)
    b = Budget(limits=Limits(tool_result=500), count=one_char)
    b.tool_result(text)
    b.tool_result("kısa")
    assert b.truncated_tool_results == 1


def test_default_limits_match_adr_004() -> None:
    assert Limits() == Limits(
        run_soft=600_000, run_hard=1_000_000, specialist=120_000, tool_result=8_000
    )


# ---- öncelik kuyruğu


async def test_gate_limits_concurrency_and_prefers_critical() -> None:
    gate = PriorityGate(2)
    order: list[str] = []
    release = asyncio.Event()

    async def run(name: str, prio: int) -> None:
        async with gate.slot(prio):
            order.append(name)
            await release.wait()

    first = [asyncio.create_task(run(n, 1)) for n in ("w1", "w2")]
    await asyncio.sleep(0)
    later = [asyncio.create_task(run("w3", 1)), asyncio.create_task(run("info", 2))]
    await asyncio.sleep(0)
    crit = asyncio.create_task(run("crit", 0))
    await asyncio.sleep(0)
    assert order == ["w1", "w2"] and gate.active == 2 and gate.waiting == 3
    release.set()
    await asyncio.gather(*first, *later, crit)
    assert order == ["w1", "w2", "crit", "w3", "info"] and gate.active == 0


async def test_gate_cancelled_waiter_does_not_leak_slot() -> None:
    gate = PriorityGate(1)
    hold = asyncio.Event()

    async def run() -> None:
        async with gate.slot():
            await hold.wait()

    a = asyncio.create_task(run())
    await asyncio.sleep(0)
    b = asyncio.create_task(run())
    await asyncio.sleep(0)
    b.cancel()
    hold.set()
    await a
    with pytest.raises(asyncio.CancelledError):
        await b
    assert gate.active == 0 and gate.waiting == 0


@pytest.mark.parametrize(
    ("labels", "prio"),
    [({"severity": "critical"}, 0), ({"severity": "Warning"}, 1), ({}, 2)],
)
def test_alert_priority(labels: dict[str, str], prio: int) -> None:
    assert alert_priority({"labels": labels}) == prio
