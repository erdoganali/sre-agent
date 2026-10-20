"""Chaos senaryoları (T1.8): beklenen sonuç dosyaları geçerli ve gerçek adlara bağlı."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from sre_agent.packs import CheckRegistry, load_pack
from sre_agent.taxonomy import load as load_taxonomy

ROOT = Path(__file__).parents[1]
SCENARIOS = sorted(p for p in (ROOT / "scenarios").iterdir() if (p / "expected.yaml").is_file())
CHECKS = set(CheckRegistry([load_pack(ROOT / "packs/vectordb")]).checks)
CATEGORIES = {c.id for c in load_taxonomy().categories}
ALERTS = {
    name
    for f in (
        "deploy/helm/vectordb-alerts/templates/rules.yaml",
        "deploy/helm/sre-lab-alerts/templates/rules.yaml",
    )
    for name in re.findall(r"-\s+alert:\s*([A-Za-z0-9_]+)", (ROOT / f).read_text())
}
LITMUS_NAMESPACES = set(
    yaml.safe_load((ROOT / "deploy/values/gke/litmus-scope.yaml").read_text())["namespaces"]
)
EXPERIMENTS = {
    "pod-delete",
    "pod-memory-hog",
    "pod-cpu-hog",
    "pod-network-latency",
    "pod-network-loss",
}


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((path / "expected.yaml").read_text())
    return data


def test_there_are_scenarios() -> None:
    assert len(SCENARIOS) >= 9


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.name)
def test_expected_file_links_are_real(path: Path) -> None:
    s = load(path)
    exp = s["expected"]
    assert s["id"] == path.name and s["title"]
    assert s["verify"] in {"lab", "loadtest", "monitoring"} or s["verify"].startswith("tenant:")
    assert exp["category"] in CATEGORIES
    assert set(exp["alerts"]) <= ALERTS, set(exp["alerts"]) - ALERTS
    assert set(exp["evidence"]) <= CHECKS, set(exp["evidence"]) - CHECKS
    assert exp["root_cause"]
    assert (path / "inject.sh").is_file() and (path / "cleanup.sh").is_file()


@pytest.mark.parametrize(
    "path", [p for p in SCENARIOS if "litmus" in load(p)["tool"]], ids=lambda p: p.name
)
def test_litmus_engine_targets_allowed_namespace(path: Path) -> None:
    engine = yaml.safe_load((path / "engine.yaml").read_text())
    assert engine["kind"] == "ChaosEngine"
    assert engine["metadata"]["namespace"] in LITMUS_NAMESPACES
    assert engine["spec"]["appinfo"]["appns"] == engine["metadata"]["namespace"]
    assert engine["spec"]["chaosServiceAccount"] == "litmus-admin"
    assert {e["name"] for e in engine["spec"]["experiments"]} <= EXPERIMENTS
