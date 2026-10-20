"""Olay gruplama kuralları (T3.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sre_agent.incidents import Incident, Signature, group

T0 = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)


def sig(
    rid: str,
    minute: int,
    ns: str | None,
    anomalies: set[str],
    lost: set[str] | None = None,
    team: str | None = "vectordb",
) -> Signature:
    return Signature(
        rid, T0 + timedelta(minutes=minute), ns, frozenset(anomalies), frozenset(lost or ()), team
    )


def ids(incidents: list[Incident]) -> list[list[str]]:
    return [[m.report_id for m in i.members] for i in incidents]


def test_same_node_loss_joins_tenants_into_one_incident() -> None:
    lost = {"nx71@2026-10-09T13:58:00"}
    out = group(
        [
            sig("a1", 0, "acme", {"pod_restarts", "node_recreated"}, lost),
            sig("l1", 1, "loadtest", {"crash_log_exception", "node_recreated"}, lost),
            sig("a2", 3, "acme", {"golden_signals_baseline"}, lost),
        ]
    )
    assert ids(out) == [["a1", "l1", "a2"]]
    assert out[0].namespaces == ["acme", "loadtest"]


def test_different_losses_of_same_node_are_different_incidents() -> None:
    out = group(
        [
            sig("x1", 0, "sre-lab", {"a"}, {"nx71@19:53"}),
            sig("x2", 20, "sre-lab", {"b"}, {"nx71@20:14"}),
        ]
    )
    assert ids(out) == [["x1"], ["x2"]]


def test_same_namespace_similar_evidence_joins_otherwise_not() -> None:
    out = group(
        [
            sig("p1", 0, "acme", {"pod_restarts", "crash_log_exception", "node_events"}),
            sig("p2", 10, "acme", {"pod_restarts", "crash_log_exception"}),  # 2/3 benzer
            sig("q1", 12, "acme", {"memory_near_limit"}),  # benzemiyor
            sig("p3", 15, "loadtest", {"pod_restarts", "crash_log_exception"}),  # başka ns
        ]
    )
    assert ids(out) == [["p1", "p2"], ["q1"], ["p3"]]


def test_window_closes_incident() -> None:
    out = group(
        [
            sig("w1", 0, "acme", {"pod_restarts"}),
            sig("w2", 25, "acme", {"pod_restarts"}),
            sig("w3", 56, "acme", {"pod_restarts"}),  # son üyeden 31 dk sonra
        ]
    )
    assert ids(out) == [["w1", "w2"], ["w3"]]


def test_different_owner_or_no_namespace_does_not_join_on_node_loss() -> None:
    lost = {"nx71@2026-10-08T19:53:00"}
    out = group(
        [
            sig("s1", 0, "acme", {"pod_restarts"}, lost),
            sig("lab", 2, "sre-lab", {"pod_restarts"}, lost, team="sre-lab"),
            sig("kafka", 3, None, {"kafka_consumer_lag"}, lost),
        ]
    )
    assert ids(out) == [["s1"], ["lab"], ["kafka"]]


def test_common_anomalies_are_majority_evidence() -> None:
    (inc,) = group(
        [
            sig("c1", 0, "acme", {"a", "b"}),
            sig("c2", 1, "acme", {"a", "b", "c"}),
            sig("c3", 2, "acme", {"a", "b"}),
        ]
    )
    assert inc.common_anomalies == ["a", "b"]
