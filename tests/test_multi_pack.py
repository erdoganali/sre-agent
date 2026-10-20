"""Çoklu paket (T1.18): iki ürün aynı çekirdekte, doğru kontroller ve doğru paket bilgisiyle."""

import re
from pathlib import Path
from typing import Any

import yaml

from fakes import END, FakeK8s, FakeMetrics
from sre_agent.config import AppConfig
from sre_agent.packs import CheckRegistry, load_pack
from sre_agent.ports import Capability, Sample
from sre_agent.service.app import _label_page
from sre_agent.service.runner import ReportRunner

ROOT = Path(__file__).parents[1]
VECTORDB = load_pack(ROOT / "packs/vectordb")
EXAMPLE = load_pack(ROOT / "packs/example")


def runner() -> ReportRunner:
    registry = CheckRegistry([VECTORDB, EXAMPLE])
    ports = {
        Capability.K8S: FakeK8s({"Pod": []}),
        Capability.METRICS: FakeMetrics(resolver=lambda q: [Sample({}, 1.0)]),
    }
    return ReportRunner(
        AppConfig(default_checks=["pod_restarts"]), registry, ports, now=lambda: END
    )


def alert(name: str, ns: str) -> dict[str, Any]:
    return {
        "status": "firing",
        "fingerprint": name,
        "labels": {"alertname": name, "namespace": ns},
        "startsAt": END.isoformat().replace("+00:00", "Z"),
    }


async def test_each_product_gets_its_checks_and_pack() -> None:
    r = runner()
    r.submit(
        {"alerts": [alert("ExampleErrorRateHigh", "orders"), alert("MilvusWriteDenied", "acme")]}
    )
    await r.wait_idle()
    by_alert = {rep.alert.name: rep for rep in r.reports.values()}
    example, milvus = by_alert["ExampleErrorRateHigh"], by_alert["MilvusWriteDenied"]
    assert example.packs == ("example",) and milvus.packs == ("vectordb",)
    assert {e.check for e in example.evidence} == {
        "example_error_rate",
        "pod_restarts",
        "recent_rollouts",
        "crash_log_exception",
    }
    assert "milvus_querynode_memory_quota" in {e.check for e in milvus.evidence}
    assert any(e.check == "example_error_rate" and e.status == "anomaly" for e in example.evidence)


async def test_unmapped_alert_has_no_pack() -> None:
    r = runner()
    r.submit({"alerts": [alert("SomethingElse", "x")]})
    await r.wait_idle()
    (rep,) = r.reports.values()
    assert rep.packs == () and {e.check for e in rep.evidence} == {"pod_restarts"}


def test_freshness_probes_come_from_packs() -> None:
    assert {p.name for p in runner().probes} == {"milvus-telemetry", "example-telemetry"}


def test_category_notes_in_label_form_are_escaped() -> None:
    notes = CheckRegistry([VECTORDB, EXAMPLE]).category_notes
    assert {pack for items in notes.values() for pack, _ in items} == {"vectordb", "example"}
    page = _label_page("r1", "A", None, notes={"database": [("x", "<script>kötü</script>")]})
    assert "<script>kötü" not in page and "&lt;script&gt;" in page


def test_every_configured_pack_is_routed_to_sre_agent() -> None:
    """Paketin alert_matchers'ı Alertmanager'da sre-agent alıcısına yönlendirilmiş olmalı."""
    cfg = yaml.safe_load((ROOT / "config/gke.yaml").read_text())
    values = yaml.safe_load((ROOT / "deploy/values/gke/kube-prometheus-stack.yaml").read_text())
    routes = values["alertmanager"]["config"]["route"]["routes"]
    routed = {
        re.sub(r"\s+", "", m)
        for route in routes
        if route.get("receiver") == "sre-agent"
        for m in route.get("matchers", [])
    }
    for p in cfg["packs"]:
        pack = load_pack(ROOT / p["path"])
        assert pack.manifest.alert_matchers, f"{pack.manifest.name}: alert_matchers boş"
        for m in pack.manifest.alert_matchers:
            assert re.sub(r"\s+", "", m) in routed, f"{m} sre-agent'a yönlendirilmemiş"


def test_core_does_not_name_a_product() -> None:
    """Çekirdek (src/sre_agent) hiçbir ürün adını, yorumlar dahil, içermez."""
    hits = [
        f"{path.relative_to(ROOT)}:{i}"
        for path in (ROOT / "src/sre_agent").rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".yaml", ".mako"}
        # bilgi tabanının veritabanı adaptörü; adı izlenen ürün değil, kütüphane (ADR-034)
        and path != ROOT / "src/sre_agent/adapters/vector_store.py"
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"milvus|vectordb", line, re.IGNORECASE)
    ]
    assert hits == []
