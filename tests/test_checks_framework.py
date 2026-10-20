import asyncio
from typing import ClassVar

import pytest

from fakes import WINDOW, FakeLogs, FakeMetrics, ctx
from sre_agent.checks import Check, CheckContext, Evidence, Status, Window, run_checks
from sre_agent.ports import Capability


class Quick(Check):
    name = "quick"
    capability = Capability.METRICS
    categories = ("network",)

    async def run(self, ctx: CheckContext) -> Evidence:
        ctx.metrics()
        return self.evidence(ctx, Status.OK, "tamam")


class Slow(Check):
    name = "slow"
    capability = Capability.METRICS
    categories = ("network",)
    timeout_s: ClassVar[float] = 0.05

    async def run(self, ctx: CheckContext) -> Evidence:
        await asyncio.sleep(1)
        return self.evidence(ctx, Status.OK, "yetişemez")


class Boom(Check):
    name = "boom"
    capability = Capability.LOGS
    categories = ("application_service_bug",)

    async def run(self, ctx: CheckContext) -> Evidence:
        raise RuntimeError("patladı")


class NeedsK8s(Check):
    name = "needs_k8s"
    capability = Capability.K8S
    categories = ("hardware_problem",)

    async def run(self, ctx: CheckContext) -> Evidence:
        await ctx.k8s().list("Node")
        return self.evidence(ctx, Status.OK, "tamam")


async def test_runner_isolates_failures_and_keeps_order() -> None:
    results = await run_checks(
        [Slow(), Quick(), Boom(), NeedsK8s()], ctx(metrics=FakeMetrics(), logs=FakeLogs())
    )
    assert [r.check for r in results] == ["slow", "quick", "boom", "needs_k8s"]
    by = {r.check: r for r in results}
    assert by["quick"].status is Status.OK
    assert by["slow"].status is Status.ERROR and "zaman aşımı" in (by["slow"].error or "")
    assert by["boom"].status is Status.ERROR and by["boom"].error == "RuntimeError: patladı"
    assert by["needs_k8s"].status is Status.ERROR and "adaptör yok" in by["needs_k8s"].summary
    assert all(r.window == WINDOW and r.duration_ms >= 0 for r in results)


async def test_checks_run_in_parallel() -> None:
    class Sleepy(Check):
        name = "sleepy"
        capability = Capability.METRICS
        categories = ("network",)

        async def run(self, ctx: CheckContext) -> Evidence:
            await asyncio.sleep(0.2)
            return self.evidence(ctx, Status.OK, "tamam")

    loop = asyncio.get_running_loop()
    started = loop.time()
    await run_checks([Sleepy() for _ in range(5)], ctx(metrics=FakeMetrics()))
    assert loop.time() - started < 0.6


def test_check_categories_must_exist_in_taxonomy() -> None:
    with pytest.raises(TypeError, match="geçersiz kategoriler"):

        class Bad(Check):
            name = "bad"
            capability = Capability.METRICS
            categories = ("not_a_category",)

            async def run(self, ctx: CheckContext) -> Evidence:
                raise NotImplementedError


def test_wrong_adapter_type_is_rejected() -> None:
    with pytest.raises(TypeError, match="MetricsPort"):
        ctx(metrics=object()).metrics()


def test_window_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="pencere"):
        Window(start=WINDOW.end, end=WINDOW.start)


def test_evidence_data_must_be_json() -> None:
    with pytest.raises(ValueError):
        Quick().evidence(ctx(), Status.OK, "x", data={"bad": object()})
