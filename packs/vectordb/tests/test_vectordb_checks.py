from pathlib import Path
from typing import Any

from fakes import FakeK8s, FakeMetrics, ctx
from sre_agent.checks import Check, Status
from sre_agent.packs import load_pack
from sre_agent.ports import Sample

PACK = load_pack(Path(__file__).parents[1])
CHECKS: dict[str, type[Check]] = {c.name: c for c in PACK.content.checks}
NS = {"namespace": "acme"}


def s(value: float, **labels: str) -> Sample:
    return Sample(labels=labels, value=value)


def metrics(**by_key: list[Sample]) -> FakeMetrics:
    """Sorgu metni anahtarı içeriyorsa o yanıt döner (ilk eşleşen)."""

    def resolve(q: str) -> list[Sample]:
        for key, answer in by_key.items():
            if key in q:
                return answer
        return []

    return FakeMetrics(resolver=resolve)


async def run(name: str, labels: dict[str, str] | None = None, **ports: Any) -> Any:
    return await CHECKS[name]().run(ctx(labels if labels is not None else NS, **ports))


async def test_querynode_memory_quota() -> None:
    near = metrics(container_memory=[s(0.93, pod="qn-0")], high_water=[s(0.95)], force_deny=[s(0)])
    ev = await run("milvus_querynode_memory_quota", metrics=near)
    assert ev.status is Status.ANOMALY and "qn-0" in ev.summary and "%95" in ev.summary

    denied = metrics(container_memory=[s(0.5, pod="qn-0")], force_deny=[s(3)])
    ev = await run("milvus_querynode_memory_quota", metrics=denied)
    assert ev.status is Status.ANOMALY and "reddetti" in ev.summary

    ok = metrics(container_memory=[s(0.5, pod="qn-0")])
    assert (await run("milvus_querynode_memory_quota", metrics=ok)).status is Status.OK

    # Alarmla aynı düzey: %80 üstü anomali, altı değil (T1.8 bulgusu)
    warn = metrics(container_memory=[s(0.82, pod="qn-0")], high_water=[s(0.95)])
    assert (await run("milvus_querynode_memory_quota", metrics=warn)).status is Status.ANOMALY
    below = metrics(container_memory=[s(0.78, pod="qn-0")], high_water=[s(0.95)])
    assert (await run("milvus_querynode_memory_quota", metrics=below)).status is Status.OK
    assert (
        await run("milvus_querynode_memory_quota", metrics=FakeMetrics())
    ).status is Status.NO_DATA


def milvus(status: str, **conds: str) -> dict[str, Any]:
    conditions = [{"type": t, "status": v, "reason": f"R{t}"} for t, v in conds.items()]
    return {
        "metadata": {"name": "cus-acme"},
        "status": {"status": status, "conditions": conditions},
    }


async def test_milvus_health() -> None:
    bad = FakeK8s(
        {"milvus.io/Milvus": [milvus("Unhealthy", MilvusReady="True", StorageReady="False")]}
    )
    ev = await run("milvus_health", k8s=bad)
    assert ev.status is Status.ANOMALY and "StorageReady=RStorageReady" in ev.summary
    good = FakeK8s({"milvus.io/Milvus": [milvus("Healthy", MilvusReady="True")]})
    assert (await run("milvus_health", k8s=good)).status is Status.OK
    assert (await run("milvus_health", k8s=FakeK8s())).status is Status.NO_DATA


async def test_coordinators() -> None:
    down = metrics(querynode_num=[s(0)], datanode_num=[s(1)], heartbeat=[s(120, node_id="7")])
    ev = await run("milvus_coordinators", metrics=down)
    assert (
        ev.status is Status.ANOMALY and "querynode yok" in ev.summary and "heartbeat" in ev.summary
    )
    ok = metrics(querynode_num=[s(2)], datanode_num=[s(1)], heartbeat=[s(3, node_id="7")])
    assert (await run("milvus_coordinators", metrics=ok)).status is Status.OK


async def test_etcd() -> None:
    bad = metrics(
        kube_pod_info=[s(3)],
        kube_pod_status_ready=[s(0, pod="etcd-1")],
        restarts_total=[s(2, pod="etcd-1")],
        meta_op_count=[s(4)],
    )
    ev = await run("milvus_etcd", metrics=bad)
    assert ev.status is Status.ANOMALY and "etcd-1" in ev.summary and "4 meta" in ev.summary
    ok = metrics(kube_pod_info=[s(3)])
    assert (await run("milvus_etcd", metrics=ok)).status is Status.OK
    assert (await run("milvus_etcd", metrics=FakeMetrics())).status is Status.NO_DATA


async def test_object_storage() -> None:
    bad = metrics(
        **{'status="fail"': [s(30)], "success|fail": [s(1000)], "latency_bucket": [s(3500)]}
    )
    ev = await run("milvus_object_storage", metrics=bad)
    assert ev.status is Status.ANOMALY and "%3" in ev.summary and "3500 ms" in ev.summary
    few = metrics(
        **{'status="fail"': [s(2)], "success|fail": [s(10)], "latency_bucket": [s(float("nan"))]}
    )
    assert (await run("milvus_object_storage", metrics=few)).status is Status.OK


async def test_segment_backlog_and_connections_are_no_data_without_metrics() -> None:
    assert (await run("milvus_segment_backlog", metrics=FakeMetrics())).status is Status.NO_DATA
    assert (await run("milvus_connections", metrics=FakeMetrics())).status is Status.NO_DATA
    backlog = metrics(segment_num=[s(500)])
    assert (await run("milvus_segment_backlog", metrics=backlog)).status is Status.ANOMALY
    conns = metrics(connection_num=[s(9500, pod="proxy-0")])
    ev = await run("milvus_connections", metrics=conns)
    assert ev.status is Status.ANOMALY and "9500/10000" in ev.summary


async def test_namespace_required() -> None:
    for name in CHECKS:
        ev = await run(name, {}, metrics=FakeMetrics(), k8s=FakeK8s())
        assert ev.status is Status.NO_DATA, name
