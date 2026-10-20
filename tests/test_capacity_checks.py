from datetime import timedelta
from typing import Any

import pytest

from fakes import END, FakeK8s, FakeMetrics, ctx
from sre_agent.checks import Status
from sre_agent.checks.builtin import (
    CpuThrottling,
    EphemeralStorageUsage,
    GoldenSignalsBaseline,
    HpaSaturation,
    MemoryNearLimit,
    NodeConditions,
    NodeEvents,
    PendingPods,
    PvcUsage,
)
from sre_agent.ports import Sample

NS = {"namespace": "sre-lab"}


def s(value: float, **labels: str) -> Sample:
    return Sample(labels=labels, value=value)


def iso(dt: Any) -> str:
    return str(dt.isoformat().replace("+00:00", "Z"))


# ------------------------------------------------------------------ golden signals


def golden(now: dict[str, float], base: dict[str, float], week: bool) -> FakeMetrics:
    def resolve(q: str) -> list[Sample]:
        if q.startswith("count(up"):
            return [s(1)] if (week or "1d" in q) else []
        vals = base if " offset " in q else now
        for name, v in vals.items():
            if name in q:
                return [s(v)]
        return []

    return FakeMetrics(resolver=resolve)


async def test_golden_signals_anomaly_uses_weekly_baseline() -> None:
    m = golden({"cpu_usage": 2.0, "working_set": 1e8}, {"cpu_usage": 0.5, "working_set": 9e7}, True)
    ev = await GoldenSignalsBaseline().run(ctx(NS, metrics=m))
    assert ev.status is Status.ANOMALY and "1w" in ev.summary and "cpu_cores" in ev.summary
    assert isinstance(ev.data, dict) and ev.data["baseline_offset"] == "1w"
    assert ev.query and 'namespace="sre-lab"' in ev.query and "{ns}" not in ev.query


async def test_golden_signals_falls_back_to_daily_and_ignores_tiny_values() -> None:
    m = golden(
        {"cpu_usage": 0.03}, {"cpu_usage": 0.001}, False
    )  # 30x ama mutlak alt sınırın altında
    ev = await GoldenSignalsBaseline().run(ctx(NS, metrics=m))
    assert ev.status is Status.OK and "1d" in ev.summary


async def test_golden_signals_without_history_is_no_data() -> None:
    ev = await GoldenSignalsBaseline().run(ctx(NS, metrics=FakeMetrics()))
    assert ev.status is Status.NO_DATA


# ------------------------------------------------------------------ container kaynakları


async def test_cpu_throttling_threshold_and_nan() -> None:
    m = FakeMetrics(
        resolver=lambda q: [
            s(0.6, pod="a", container="app"),
            s(0.1, pod="b", container="app"),
            s(float("nan"), pod="c", container="app"),
        ]
    )
    ev = await CpuThrottling().run(ctx(NS, metrics=m))
    assert ev.status is Status.ANOMALY and "a/app %60" in ev.summary
    assert isinstance(ev.data, list) and len(ev.data) == 1
    ok = FakeMetrics(resolver=lambda q: [s(0.2, pod="a", container="app")])
    assert (await CpuThrottling().run(ctx(NS, metrics=ok))).status is Status.OK
    assert (await CpuThrottling().run(ctx(NS, metrics=FakeMetrics()))).status is Status.NO_DATA


async def test_memory_near_limit() -> None:
    m = FakeMetrics(resolver=lambda q: [s(0.95, pod="q", container="querynode"), s(0.5, pod="p")])
    ev = await MemoryNearLimit().run(ctx(NS, metrics=m))
    assert ev.status is Status.ANOMALY and "q/querynode %95" in ev.summary
    ok = FakeMetrics(resolver=lambda q: [s(0.5, pod="p")])
    assert (await MemoryNearLimit().run(ctx(NS, metrics=ok))).status is Status.OK


async def test_hpa_saturation() -> None:
    # HPA var ama doygun değil: yalnızca current_replicas içermeyen "exists" sorgusu cevap verir.
    web = [s(5, horizontalpodautoscaler="web")]
    sat = FakeMetrics(resolver=lambda q: [] if "current_replicas" in q else web)
    both = FakeMetrics(resolver=lambda q: [s(5, horizontalpodautoscaler="web")])
    assert (await HpaSaturation().run(ctx(NS, metrics=both))).status is Status.ANOMALY
    assert (await HpaSaturation().run(ctx(NS, metrics=sat))).status is Status.OK
    assert (await HpaSaturation().run(ctx(NS, metrics=FakeMetrics()))).status is Status.NO_DATA


# ------------------------------------------------------------------ disk


async def test_pvc_usage_ratio_and_fill_prediction() -> None:
    def resolve(q: str) -> list[Sample]:
        if "predict_linear" in q:
            return [s(-1, persistentvolumeclaim="logs"), s(-1, persistentvolumeclaim="etcd")]
        return [
            s(0.85, persistentvolumeclaim="data"),
            s(0.6, persistentvolumeclaim="logs"),
            s(0.04, persistentvolumeclaim="etcd"),  # yeni oluşmuş disk: hızlı büyüme yanıltır
        ]

    ev = await PvcUsage().run(ctx(NS, metrics=FakeMetrics(resolver=resolve)))
    assert ev.status is Status.ANOMALY
    assert "data %85" in ev.summary and "logs %60 (24 sa içinde dolacak)" in ev.summary
    assert "etcd" not in ev.summary


async def test_pvc_fill_prediction_on_fresh_volume_is_not_anomaly() -> None:
    def resolve(q: str) -> list[Sample]:
        if "predict_linear" in q:
            return [s(-1, persistentvolumeclaim="data-etcd-0")]
        return [s(0.04, persistentvolumeclaim="data-etcd-0")]

    ev = await PvcUsage().run(ctx(NS, metrics=FakeMetrics(resolver=resolve)))
    assert ev.status is Status.OK


async def test_ephemeral_storage_pod_and_emptydir() -> None:
    def resolve(q: str) -> list[Sample]:
        if "volume_limit_percentage" in q:
            return [s(85, pod_name="proxy", volume_name="access-logs")]
        return [s(0.5 * 1024**3, pod_name="proxy")]

    ev = await EphemeralStorageUsage().run(ctx(NS, metrics=FakeMetrics(resolver=resolve)))
    assert ev.status is Status.ANOMALY
    assert isinstance(ev.data, list) and ev.data[0] == {
        "pod": "proxy",
        "volume": "access-logs",
        "percent": 85,
    }
    ok = FakeMetrics(
        resolver=lambda q: [s(0.1 * 1024**3, pod_name="p")] if "pod_usage" in q else []
    )
    assert (await EphemeralStorageUsage().run(ctx(NS, metrics=ok))).status is Status.OK


async def test_queries_are_overridable_and_validated() -> None:
    check = EphemeralStorageUsage(queries={"pod": "custom_metric{{ns='{ns}'}}"})
    m = FakeMetrics(resolver=lambda q: [])
    await check.run(ctx(NS, metrics=m))
    assert "custom_metric{ns='sre-lab'}" in m.queries
    with pytest.raises(ValueError, match="bilinmeyen sorgu"):
        EphemeralStorageUsage(queries={"nope": "x"})


async def test_namespace_required() -> None:
    for check in (CpuThrottling(), MemoryNearLimit(), PvcUsage(), PendingPods()):
        ev = await check.run(ctx({}, metrics=FakeMetrics(), k8s=FakeK8s()))
        assert ev.status is Status.NO_DATA


# ------------------------------------------------------------------ Kubernetes


async def test_pending_pods_with_scheduling_reason() -> None:
    old = iso(END - timedelta(minutes=20))
    fresh = iso(END - timedelta(minutes=1))
    k8s = FakeK8s(
        {
            "Pod": [
                {"metadata": {"name": "stuck", "creationTimestamp": old}},
                {"metadata": {"name": "new", "creationTimestamp": fresh}},
            ],
            "Event": [
                {
                    "involvedObject": {"kind": "Pod", "name": "stuck"},
                    "message": "0/3 nodes are available: 3 Insufficient memory.",
                }
            ],
        }
    )
    ev = await PendingPods().run(ctx(NS, k8s=k8s))
    assert ev.status is Status.ANOMALY and "1 pod 5 dk" in ev.summary
    assert isinstance(ev.data, list) and "Insufficient memory" in str(ev.data[0])


def node(name: str, **conditions: str) -> dict[str, Any]:
    conds = [{"type": t, "status": v} for t, v in {"Ready": "True", **conditions}.items()]
    return {"metadata": {"name": name}, "status": {"conditions": conds}}


def pod_on(n: str) -> dict[str, Any]:
    return {"metadata": {"name": f"p-{n}"}, "spec": {"nodeName": n}}


async def test_node_conditions_scoped_to_namespace_nodes() -> None:
    k8s = FakeK8s(
        {
            "Node": [node("n1", MemoryPressure="True"), node("n2", Ready="False"), node("n3")],
            "Pod": [pod_on("n1"), pod_on("n3")],
        }
    )
    ev = await NodeConditions().run(ctx(NS, k8s=k8s))
    assert ev.status is Status.ANOMALY and "n1 MemoryPressure=True" in ev.summary
    assert "n2" not in ev.summary  # namespace'in pod'u n2'de değil
    everything = await NodeConditions().run(ctx({}, k8s=k8s))
    assert "n2 Ready=False" in everything.summary


async def test_node_events_in_window() -> None:
    inside = iso(END - timedelta(minutes=3))
    outside = iso(END - timedelta(days=1))
    k8s = FakeK8s(
        {
            "Node": [node("n1")],
            "Event": [
                {
                    "involvedObject": {"kind": "Node", "name": "n1"},
                    "reason": "NodeNotReady",
                    "lastTimestamp": inside,
                },
                {
                    "involvedObject": {"kind": "Node", "name": "n1"},
                    "reason": "PreemptionNotice",
                    "eventTime": inside,
                },
                {
                    "involvedObject": {"kind": "Node", "name": "n1"},
                    "reason": "Rebooted",
                    "lastTimestamp": outside,
                },
                {
                    "involvedObject": {"kind": "Node", "name": "n1"},
                    "reason": "Starting",
                    "lastTimestamp": inside,
                },
            ],
        }
    )
    ev = await NodeEvents().run(ctx({}, k8s=k8s))
    assert ev.status is Status.ANOMALY
    assert "NodeNotReady" in ev.summary and "PreemptionNotice" in ev.summary
    assert "Rebooted" not in ev.summary and "Starting" not in ev.summary


# ------------------------------------------------------------------ node kaybı


def node_metrics(**by_kind: list[str]) -> FakeMetrics:
    """by_kind: gone / not_ready / recreated / created -> node adları."""

    def resolve(q: str) -> list[Sample]:
        if q.startswith("count(kube_node_info)"):
            return [s(3)]
        kind = (
            "gone"
            if "unless" in q
            else "not_ready"
            if "kube_node_status_condition" in q
            else "recreated"
            if "changes(" in q
            else "created"
        )
        value = 2 if kind == "recreated" else (END.timestamp() - 60 if kind == "created" else 1)
        return [s(value, node=n) for n in by_kind.get(kind, [])]

    return FakeMetrics(resolver=resolve)


async def test_node_recreated_same_name_is_not_new_node() -> None:
    from sre_agent.checks.builtin import NodeRecreated

    ev = await NodeRecreated().run(
        ctx(NS, metrics=node_metrics(not_ready=["a"], recreated=["a"], created=["a"]))
    )
    assert ev.status is Status.ANOMALY
    assert ev.summary == "pencerede node kaybı: a hazır değildi/yeniden oluştu (2 kez)"
    assert "hardware_problem" in NodeRecreated.categories


async def test_node_gone_before_recreation_is_seen() -> None:
    from sre_agent.checks.builtin import NodeRecreated

    # node gitti, yeni nesne henüz oluşmadı (rapor arada yazıldı)
    ev = await NodeRecreated().run(ctx(NS, metrics=node_metrics(gone=["b"], created=["c"])))
    assert ev.status is Status.ANOMALY and "b gitti" in ev.summary and "c yeni node" in ev.summary


async def test_node_recreated_ok_and_no_data() -> None:
    from sre_agent.checks.builtin import NodeRecreated

    ev = await NodeRecreated().run(ctx(NS, metrics=node_metrics()))
    assert ev.status is Status.OK
    ev = await NodeRecreated().run(ctx(NS, metrics=FakeMetrics()))
    assert ev.status is Status.NO_DATA


async def test_node_recreated_scoped_to_namespace_nodes() -> None:
    from sre_agent.checks.builtin import NodeRecreated

    m = node_metrics(not_ready=["a"])
    await NodeRecreated().run(ctx(NS, metrics=m))
    scoped = [q for q in m.queries if "kube_node" in q and "count(" not in q]
    assert scoped and all('kube_pod_info{namespace="sre-lab"' in q for q in scoped)
    m2 = node_metrics(not_ready=["a"])
    await NodeRecreated().run(ctx({}, metrics=m2))
    assert not any("kube_pod_info" in q for q in m2.queries)  # namespace yoksa kümenin tamamı
