from datetime import timedelta
from typing import Any

from fakes import END, FakeK8s, FakeMetrics, ctx
from sre_agent.checks import Status
from sre_agent.checks.builtin import KafkaConsumerLag, PodRestarts
from sre_agent.checks.builtin.kafka import BASELINE, LAG
from sre_agent.ports import Sample


def pod(name: str, finished: str | None, reason: str = "OOMKilled") -> dict[str, Any]:
    last = {"terminated": {"reason": reason, "exitCode": 137, "finishedAt": finished}}
    return {
        "metadata": {"name": name},
        "status": {
            "containerStatuses": [
                {"name": "app", "restartCount": 3, "lastState": last if finished else {}}
            ]
        },
    }


def iso(dt: Any) -> str:
    return str(dt.isoformat().replace("+00:00", "Z"))


async def test_pod_restarts_in_window_is_anomaly() -> None:
    inside = iso(END - timedelta(minutes=5))
    outside = iso(END - timedelta(hours=3))
    k8s = FakeK8s({"Pod": [pod("a", inside), pod("b", outside), pod("c", None)]})
    ev = await PodRestarts().run(ctx({"namespace": "sre-lab"}, k8s=k8s))
    assert ev.status is Status.ANOMALY
    assert "OOMKilled" in ev.summary
    assert isinstance(ev.data, list)
    assert [r["pod"] for r in ev.data if isinstance(r, dict)] == ["a"]


async def test_pod_restarts_ok_and_no_data() -> None:
    k8s = FakeK8s({"Pod": [pod("c", None)]})
    assert (await PodRestarts().run(ctx({"namespace": "x"}, k8s=k8s))).status is Status.OK
    assert (
        await PodRestarts().run(ctx({"namespace": "x"}, k8s=FakeK8s()))
    ).status is Status.NO_DATA
    assert (await PodRestarts().run(ctx({}, k8s=k8s))).status is Status.NO_DATA


def lag(group: str, topic: str, value: float) -> Sample:
    return Sample(labels={"consumergroup": group, "topic": topic}, value=value)


async def test_kafka_lag_above_twice_baseline_is_anomaly() -> None:
    metrics = FakeMetrics(
        {
            LAG: [lag("tc", "vectordb_milvus_metrics", 500), lag("tc", "vectordb_auditlog", 3)],
            BASELINE: [
                lag("tc", "vectordb_milvus_metrics", 20),
                lag("tc", "vectordb_auditlog", 0),
            ],
        }
    )
    ev = await KafkaConsumerLag().run(ctx(metrics=metrics))
    assert ev.status is Status.ANOMALY
    assert "tc/vectordb_milvus_metrics lag 500" in ev.summary
    assert ev.query and LAG in ev.query


async def test_kafka_lag_small_or_near_baseline_is_ok() -> None:
    metrics = FakeMetrics(
        {LAG: [lag("tc", "t1", 50), lag("tc", "t2", 900)], BASELINE: [lag("tc", "t2", 800)]}
    )
    assert (await KafkaConsumerLag().run(ctx(metrics=metrics))).status is Status.OK


async def test_kafka_lag_without_metric_is_no_data() -> None:
    assert (await KafkaConsumerLag().run(ctx(metrics=FakeMetrics()))).status is Status.NO_DATA
