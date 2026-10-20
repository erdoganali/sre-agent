"""Senaryo karşılaştırması (T1.9)."""

from pathlib import Path
from typing import Any

import pytest

from sre_agent.eval.compare import Expected, Verdict, evaluate, load_scenario

EXP = Expected(
    alerts=["LabContainerWaiting"],
    alert_within_min=4,
    category="human_error",
    root_cause="ConfigMap elle bozuldu",
    evidence=["manual_changes"],
)


def report(*anomalies: str, categories: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "evidence": [{"check": c, "status": "anomaly"} for c in anomalies]
        + [{"check": "pod_restarts", "status": "ok"}],
        "anomalies_by_category": categories or {},
    }


def test_pass_when_alert_evidence_and_category_match() -> None:
    out = evaluate(
        EXP,
        {"LabContainerWaiting"},
        report("manual_changes", "recent_rollouts", categories={"human_error": 1}),
    )
    assert out.verdict is Verdict.PASS and out.noise == ["recent_rollouts"]


def test_partial_when_evidence_missing() -> None:
    out = evaluate(EXP, {"LabContainerWaiting"}, report("recent_rollouts", categories={"x": 1}))
    assert out.verdict is Verdict.PARTIAL
    assert out.evidence_missing == ["manual_changes"] and not out.category_found


def test_fail_without_report_or_alert() -> None:
    assert evaluate(EXP, set(), None).verdict is Verdict.FAIL
    assert evaluate(EXP, {"LabContainerWaiting"}, None).verdict is Verdict.FAIL
    out = evaluate(EXP, set(), report("manual_changes", categories={"human_error": 1}))
    assert out.verdict is Verdict.FAIL and out.alerts_missing == ["LabContainerWaiting"]


@pytest.mark.parametrize(
    "path",
    sorted(p for p in (Path(__file__).parents[1] / "scenarios").iterdir() if p.is_dir()),
    ids=lambda p: p.name,
)
def test_every_scenario_parses(path: Path) -> None:
    assert load_scenario(path).id == path.name


def test_deferred_scenario_is_skipped_by_runner() -> None:
    from sre_agent.eval.runner import run_one

    scenario = load_scenario(Path(__file__).parents[1] / "scenarios/10-object-storage")
    assert scenario.expected.deferred
    r = run_one(Path("."), scenario, cluster=None, context="x", with_load=True, label=False)  # type: ignore[arg-type]
    assert r["outcome"]["verdict"] == Verdict.SKIPPED
    assert r["outcome"]["reasons"][0].startswith("ertelendi:")
