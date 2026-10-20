"""Rapor deposu, gerçek Postgres'e karşı (make test-db)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from fakes import END, WINDOW
from sre_agent.checks import Evidence, Status
from sre_agent.ports import Capability
from sre_agent.report import AlertRef, Report, build_report
from sre_agent.store import Label, Versions
from sre_agent.store.migrate import upgrade_async
from sre_agent.store.postgres import PostgresReportStore

pytestmark = pytest.mark.db
URL = os.environ.get("SRE_TEST_DATABASE_URL")
if not URL:
    pytest.skip("SRE_TEST_DATABASE_URL yok (make test-db)", allow_module_level=True)

V = Versions(app="0.1.0", taxonomy="1.0.0", packs={"vectordb": "0.1.0"})


def report(rid: str, fp: str, test: bool = False, at_offset: timedelta = timedelta()) -> Report:
    ev = Evidence(
        check="pod_restarts",
        capability=Capability.K8S,
        categories=("capacity_performance",),
        status=Status.ANOMALY,
        summary="web yeniden başladı",
        window=WINDOW,
    )
    labels = {"alertname": "PodBroken", "namespace": "sre-lab", "severity": "warning"}
    if test:
        labels["test"] = "true"
    return build_report(
        rid,
        AlertRef(name="PodBroken", fingerprint=fp, labels=labels, starts_at=END),
        WINDOW,
        END + at_offset,
        12.0,
        [ev],
        blind_spots=["milvus: son veri 900 sn önce"],
    )


@pytest.fixture
async def store() -> AsyncIterator[PostgresReportStore]:
    assert URL
    await upgrade_async(URL)
    await upgrade_async(URL)  # iki kez: migration'lar tekrar çalıştırılabilir olmalı
    s = PostgresReportStore(URL)
    async with s.engine.begin() as conn:
        await conn.exec_driver_sql("TRUNCATE reports.reports, reports.incidents CASCADE")
    yield s
    await s.aclose()


async def test_save_get_recent_and_upsert(store: PostgresReportStore) -> None:
    await store.save(report("r1", "fp1"), V)
    await store.save(report("r2", "fp2", test=True), V)
    await store.save(report("r1", "fp1"), V)  # aynı id: güncellenir, çoğalmaz
    row = await store.get("r1")
    assert row and row["anomalies"] == ["pod_restarts"] and row["taxonomy_version"] == "1.0.0"
    assert row["report"]["evidence"][0]["summary"] == "web yeniden başladı"
    assert row["blind_spots"] == ["milvus: son veri 900 sn önce"]
    recent = await store.recent(10, include_test=False)
    assert [r["id"] for r in recent] == ["r1"] and recent[0]["labels"]["alertname"] == "PodBroken"
    assert {r["id"] for r in await store.recent(10, include_test=True)} == {"r1", "r2"}
    assert await store.get("yok") is None


async def test_resolve_label_and_purge_keeps_labeled(store: PostgresReportStore) -> None:
    old = timedelta(days=-200)
    await store.save(report("old-unlabeled", "fpA", at_offset=old), V)
    await store.save(report("old-labeled", "fpB", at_offset=old), V)
    await store.save(report("new", "fpA"), V)
    assert await store.resolve("fpA", END) == 2
    assert await store.resolve("fpA", END) == 0  # zaten çözülmüş
    label = Label(
        category="capacity_performance", root_cause="OOMKilled: limit düşük", labeled_by="ali"
    )
    assert await store.set_label("old-labeled", label, END)
    assert not await store.set_label("yok", label, END)
    assert await store.purge(END - timedelta(days=180)) == 1
    ids = {r["id"] for r in await store.recent(10, include_test=True)}
    assert ids == {"old-labeled", "new"}
    row = await store.get("old-labeled")
    assert row and row["label_category"] == "capacity_performance" and row["labeled_by"] == "ali"


async def test_decisions_saved_replaced_and_history(store: PostgresReportStore) -> None:
    from sre_agent.decisions import DecisionConfig, Mode, Point
    from sre_agent.decisions.engine import DecisionEngine
    from sre_agent.taxonomy import load as load_taxonomy

    r = report("d1", "fpD")
    await store.save(r, V)
    dset = await DecisionEngine(DecisionConfig(mode=Mode.SHADOW), load_taxonomy(), None).assess(r)
    await store.save_decisions("d1", dset, END)
    await store.save_decisions("d1", dset, END)  # tekrar: çoğalmaz, yerine yazılır
    async with store.engine.connect() as conn:
        rows = (
            await conn.exec_driver_sql(
                "SELECT point, source, effective, outcome, probs, input_hash, mode"
                " FROM reports.decisions WHERE report_id='d1' ORDER BY id"
            )
        ).all()
    assert len(rows) == len(dset.decisions)
    group = next(x for x in rows if x.point == "group")
    assert group.source == "rules" and group.effective and group.mode == "shadow"
    assert group.outcome == "infrastructure" and group.probs["infrastructure"] == 1.0
    assert dset.effective(Point.CATEGORY).outcome == "capacity_performance"  # type: ignore[union-attr]

    label = Label(category="capacity_performance", root_cause="OOM", labeled_by="ali")
    await store.set_label("d1", label, END)
    hist = await store.labeled_history("PodBroken")
    assert hist == [
        {
            "id": "d1",
            "alert": "PodBroken",
            "anomaly_checks": ["pod_restarts"],
            "label_category": "capacity_performance",
        }
    ]
    await store.purge(END + timedelta(days=1))  # etiketli: kalır, kararları da
    async with store.engine.begin() as conn:
        await conn.exec_driver_sql("DELETE FROM reports.reports WHERE id='d1'")
        left = (await conn.exec_driver_sql("SELECT count(*) FROM reports.decisions")).scalar()
    assert left == 0  # rapor silinince kararları da silinir (CASCADE)


async def test_incidents_saved_and_labeled_with_members(store: PostgresReportStore) -> None:
    from sre_agent.incidents import Signature, assign

    open_: list = []  # type: ignore[type-arg]
    r1, r2 = report("i1", "fpI1"), report("i2", "fpI2", at_offset=timedelta(minutes=5))
    inc, why1 = assign(Signature.of(r1), open_)
    inc2, why2 = assign(Signature.of(r2), open_)
    assert inc is inc2 and why1 == "yeni olay" and "aynı namespace" in why2
    for r, why in ((r1, why1), (r2, why2)):
        await store.save(r.model_copy(update={"incident_id": inc.id, "incident_reason": why}), V)
    await store.save_incident(inc)
    await store.save_incident(inc)  # tekrar: güncellenir
    got = await store.get_incident(inc.id)
    assert got and got["report_count"] == 2 and got["namespaces"] == ["sre-lab"]
    assert [m["id"] for m in got["reports"]] == ["i1", "i2"]
    recent = await store.recent_reports(END - timedelta(minutes=30))
    assert {x["incident_id"] for x in recent} == {inc.id}
    label = Label(category="hardware_problem", root_cause="node kaybı", labeled_by="ali")
    assert await store.label_incident(inc.id, label, END) == 2
    row = await store.get("i2")
    assert row and row["label_category"] == "hardware_problem"
    assert row["labeled_by"] == f"ali (olay {inc.id})"


async def test_investigation_with_postgres_checkpoint(store: PostgresReportStore) -> None:
    from sre_agent.graph.investigator import GraphMode, InvestigationConfig, Investigator
    from sre_agent.graph.wiring import NoModel
    from sre_agent.incidents import Signature, assign

    open_: list = []  # type: ignore[type-arg]
    r1 = report("g1", "fpG1")
    inc, why = assign(Signature.of(r1), open_)
    await store.save(r1.model_copy(update={"incident_id": inc.id, "incident_reason": why}), V)
    await store.save_incident(inc)
    assert URL
    inv = Investigator(
        InvestigationConfig(mode=GraphMode.SHADOW),
        store,
        lambda _b: NoModel(),  # type: ignore[arg-type,return-value]
        database_url=URL,
    )
    result = await inv.run(inc.id)
    assert result["report"]["source"] == "fallback"
    got = await store.get_incident(inc.id)
    assert got and got["investigation"]["thread"] == result["thread"]
    assert got["investigation_mode"] == "shadow"
    async with store.engine.connect() as conn:
        n = (
            await conn.exec_driver_sql(
                "SELECT count(*) FROM agent.checkpoints WHERE thread_id = $1", (result["thread"],)
            )
        ).scalar()
    assert n and n > 0  # checkpoint agent şemasında
