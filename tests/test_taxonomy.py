import copy
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from sre_agent.taxonomy import Taxonomy, load

PLAN_CATEGORIES = {
    "change": {"application_release", "planned_infrastructure_change", "human_error"},
    "application": {"application_service_bug"},
    "infrastructure": {
        "capacity_performance",
        "hardware_problem",
        "infrastructure_system",
        "network",
        "database",
    },
    "external": {"vendor_external"},
    "security": {"security"},
}


def raw() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(load().model_dump_json()))


def test_shipped_taxonomy_matches_plan() -> None:
    t = load()
    assert len(t.groups) == 5
    assert len(t.categories) == 11
    assert {g.id: {c.id for c in t.in_group(g.id)} for g in t.groups} == PLAN_CATEGORIES


def test_every_category_has_definition_and_rule() -> None:
    for c in load().categories:
        assert c.definition.strip()
        assert c.rules


def test_close_categories_point_at_each_other() -> None:
    # Capacity / Hardware / Infrastructure-System ayrımı kural olarak yazılı olmalı.
    t = load()
    targets = {c.id: {r.not_this_but for r in c.rules} for c in t.categories}
    assert "hardware_problem" in targets["capacity_performance"]
    assert "infrastructure_system" in targets["capacity_performance"]
    assert "capacity_performance" in targets["hardware_problem"]
    assert "capacity_performance" in targets["infrastructure_system"]


def test_unknown_is_not_a_category() -> None:
    t = load()
    assert t.unknown.id == "unknown"
    with pytest.raises(KeyError):
        t.category("unknown")


def test_rejects_rule_to_missing_category() -> None:
    data = raw()
    data["categories"][0]["rules"][0]["not_this_but"] = "does_not_exist"
    with pytest.raises(ValidationError, match="bilinmeyen kategori"):
        Taxonomy.model_validate(data)


def test_rejects_self_reference() -> None:
    data = raw()
    c = data["categories"][0]
    c["rules"][0]["not_this_but"] = c["id"]
    with pytest.raises(ValidationError, match="kendisini"):
        Taxonomy.model_validate(data)


def test_rejects_category_without_rules() -> None:
    data = raw()
    data["categories"][0]["rules"] = []
    with pytest.raises(ValidationError):
        Taxonomy.model_validate(data)


def test_rejects_duplicate_category() -> None:
    data = raw()
    data["categories"].append(data["categories"][0])
    with pytest.raises(ValidationError, match="benzersiz"):
        Taxonomy.model_validate(data)


def test_rejects_unknown_group_and_extra_fields() -> None:
    data = raw()
    data["categories"][0]["group"] = "nowhere"
    with pytest.raises(ValidationError, match="bilinmeyen grup"):
        Taxonomy.model_validate(data)
    data = raw()
    data["categories"][0]["severity"] = "high"
    with pytest.raises(ValidationError):
        Taxonomy.model_validate(data)


def test_rejects_bad_version() -> None:
    data = raw()
    data["version"] = "v1"
    with pytest.raises(ValidationError):
        Taxonomy.model_validate(data)
