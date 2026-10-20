"""VectorDB / Milvus dashboard'u (T1.13): kontrol ile panel aynı sorguyu kullanır."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from sre_agent.packs import load_pack

PACK_DIR = Path(__file__).parents[1]
PACK = load_pack(PACK_DIR)


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "milvus_dashboard", PACK_DIR / "dashboards/build.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = _builder()
DASHBOARD: dict[str, Any] = json.loads((PACK_DIR / "dashboards/milvus.json").read_text())
PANELS = [p for p in DASHBOARD["panels"] if p["type"] != "row"]
EXPRS = {t["expr"] for p in PANELS for t in p.get("targets", [])}


def test_dashboard_file_is_up_to_date() -> None:
    assert (PACK_DIR / "dashboards/milvus.json").read_text() == BUILD.render(), "make dashboards"


def test_every_check_query_is_a_panel_query() -> None:
    """Kontrolün her sorgusu, $tenant ve $__rate_interval ile açılmış hâliyle bir panelde."""
    missing = [
        f"{c.name}.{key}"
        for c in PACK.content.checks
        for key, template in c.queries.items()
        if BUILD.expr(template) not in EXPRS
    ]
    missing += [s.name for s in PACK.content.signals if BUILD.expr(s.query) not in EXPRS]
    assert missing == []


def test_every_pack_check_has_a_panel() -> None:
    """Sorgusu olmayan (Kubernetes'ten okuyan) kontroller dahil her kontrolün paneli var."""
    described = " ".join(p.get("description", "") for p in PANELS)
    assert [c.name for c in PACK.content.checks if f"`{c.name}`" not in described] == []


def test_only_known_checks_have_no_promql() -> None:
    """milvus_health Milvus nesnesini Kubernetes API'sinden okur; paylaşılacak PromQL'i yok."""
    assert [c.name for c in PACK.content.checks if not c.queries] == ["milvus_health"]


def test_tenant_variable_and_annotations() -> None:
    names = [v["name"] for v in DASHBOARD["templating"]["list"]]
    annotations = [a["name"] for a in DASHBOARD["annotations"]["list"]]
    assert names == ["tenant"] and annotations == ["SRE raporları", "Alarmlar", "Chaos deneyleri"]
    assert all("{ns}" not in e and "{range}" not in e for e in EXPRS)
