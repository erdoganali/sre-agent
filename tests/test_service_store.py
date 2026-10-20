import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest

from fakes import END, FakeK8s
from sre_agent.config import AppConfig
from sre_agent.packs import CheckRegistry
from sre_agent.ports import Capability
from sre_agent.report import Report
from sre_agent.service.app import create_app
from sre_agent.service.runner import ReportRunner
from sre_agent.store import Label, Versions


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.resolved: list[tuple[str, datetime]] = []
        self.down = False
        self.decisions: dict[str, Any] = {}
        self.incidents: dict[str, dict[str, Any]] = {}
        self.investigations: dict[str, dict[str, Any]] = {}

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("postgres kapalı")

    async def save(self, report: Report, versions: Versions) -> None:
        self._check()
        a = report.alert
        self.rows[report.id] = {
            "id": report.id,
            "alertname": a.name,
            "namespace": a.labels.get("namespace"),
            "severity": a.labels.get("severity"),
            "labels": dict(a.labels),
            "generated_at": report.generated_at,
            "resolved_at": None,
            "anomalies": [e.check for e in report.anomalies],
            "blind_spots": list(report.blind_spots),
            "packs": list(report.packs),
            "annotation_id": report.annotation_id,
            "is_test": a.labels.get("test") == "true",
            "report": report.model_dump(mode="json"),
            "label_category": None,
            "label_root_cause": None,
            "labeled_by": None,
            "labeled_at": None,
            "taxonomy_version": versions.taxonomy,
        }

    async def save_decisions(self, report_id: str, dset: Any, at: datetime) -> None:
        self._check()
        self.decisions[report_id] = dset

    async def save_incident(self, inc: Any) -> None:
        self._check()
        self.incidents[inc.id] = {
            "id": inc.id,
            "report_count": len(inc.members),
            "namespaces": inc.namespaces,
            "label_category": None,
        }

    async def set_report_incident(self, report_id: str, incident_id: str, why: str) -> None:
        self.rows[report_id].update(incident_id=incident_id, incident_reason=why)

    async def recent_reports(self, since: datetime) -> list[dict[str, Any]]:
        self._check()
        return [
            {"id": r["id"], "report": r["report"], "incident_id": r["report"].get("incident_id")}
            for r in self.rows.values()
            if r["generated_at"] >= since
        ]

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        inc = self.incidents.get(incident_id)
        if inc is None:
            return None
        members = [r for r in self.rows.values() if r["report"].get("incident_id") == incident_id]
        inv = self.investigations.get(incident_id)
        extra = {"investigation": inv, "investigation_mode": inv["mode"]} if inv else {}
        return inc | {"reports": members} | extra

    async def label_incident(self, incident_id: str, label: Any, at: datetime) -> int:
        n = 0
        for r in self.rows.values():
            if r["report"].get("incident_id") == incident_id:
                r.update(
                    label_category=label.category,
                    label_root_cause=label.root_cause,
                    labeled_by=f"{label.labeled_by} (olay {incident_id})",
                    labeled_at=at,
                )
                n += 1
        self.incidents[incident_id]["label_category"] = label.category
        return n

    async def incident_reports(self, incident_id: str) -> list[dict[str, Any]]:
        return [
            r["report"] | {"incident_id": incident_id}
            for r in self.rows.values()
            if r["report"].get("incident_id") == incident_id
        ]

    async def save_investigation(
        self, incident_id: str, result: dict[str, Any], mode: str, at: datetime
    ) -> None:
        self.investigations[incident_id] = result | {"mode": mode}

    async def get_decisions(self, report_id: str) -> list[dict[str, Any]]:
        self._check()
        dset = self.decisions.get(report_id)
        if not dset:
            return []
        extra = {"mode": str(dset.mode), "noise_flag": dset.noise_flag}  # Postgres sütunları gibi
        return [d.model_dump(mode="json") | extra for d in dset.decisions]

    async def labeled_history(self, alertname: str, limit: int = 50) -> list[dict[str, Any]]:
        self._check()
        return [
            {
                "id": r["id"],
                "alert": r["alertname"],
                "anomaly_checks": r["anomalies"],
                "label_category": r["label_category"],
            }
            for r in self.rows.values()
            if r["alertname"] == alertname and r["label_category"]
        ][:limit]

    async def resolve(self, fingerprint: str, at: datetime) -> int:
        self._check()
        self.resolved.append((fingerprint, at))
        return 1

    async def get(self, report_id: str) -> dict[str, Any] | None:
        self._check()
        return self.rows.get(report_id)

    async def recent(self, limit: int, include_test: bool) -> list[dict[str, Any]]:
        self._check()
        rows = [r for r in self.rows.values() if include_test or not r["is_test"]]
        return rows[:limit]

    async def set_label(self, report_id: str, label: Label, at: datetime) -> bool:
        if report_id not in self.rows:
            return False
        self.rows[report_id].update(
            label_category=label.category,
            label_root_cause=label.root_cause,
            labeled_by=label.labeled_by,
            labeled_at=at,
        )
        return True

    async def purge(self, older_than: datetime) -> int:
        return 0


def make(store: FakeStore) -> ReportRunner:
    cfg = AppConfig(default_checks=["pod_restarts"])
    return ReportRunner(
        cfg, CheckRegistry(), {Capability.K8S: FakeK8s()}, now=lambda: END, store=store
    )


def alert(fp: str, status: str = "firing", name: str = "PodBroken", **extra: str) -> dict[str, Any]:
    return {
        "alerts": [
            {
                "status": status,
                "fingerprint": fp,
                "labels": {"alertname": name, "namespace": "sre-lab", **extra},
                "startsAt": (END - timedelta(minutes=2)).isoformat(),
                "endsAt": END.isoformat(),
            }
        ]
    }


async def test_report_saved_with_versions_and_resolved() -> None:
    store = FakeStore()
    r = make(store)
    r.submit(alert("fp1"))
    await r.wait_idle()
    (row,) = store.rows.values()
    assert row["taxonomy_version"] == "1.1.0"
    r.submit(alert("fp1", status="resolved"))
    await r.wait_idle()
    assert store.resolved == [("fp1", END)]


async def test_outbox_when_postgres_down_then_flush() -> None:
    store = FakeStore()
    store.down = True
    r = make(store)
    r.submit(alert("fp1"))
    await r.wait_idle()
    assert not store.rows and len(r._outbox) == 2 and len(r.reports) == 1  # rapor + olay kuyrukta
    assert await r.flush() == 0  # hâlâ kapalı: kuyrukta kalır
    store.down = False
    assert await r.flush() == 2 and len(store.rows) == 1 and not r._outbox


async def client(r: ReportRunner) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(r)), base_url="http://t")


async def test_listing_hides_test_reports_and_label_form_roundtrip() -> None:
    store = FakeStore()
    r = make(store)
    r.submit(alert("fp1"))
    r.submit(alert("fp2", test="true"))
    await r.wait_idle()
    real_id = next(k for k, v in store.rows.items() if not v["is_test"])
    async with await client(r) as c:
        listing = (await c.get("/reports")).json()
        assert [x["id"] for x in listing] == [real_id]
        assert listing[0]["labels"]["alertname"] == "PodBroken"  # etiketler listede de var
        assert len((await c.get("/reports?include_test=true")).json()) == 2
        form = (await c.get(f"/reports/{real_id}/label")).text
        assert "Application/Service Bug" in form and 'value="unknown"' in form
        resp = await c.post(
            f"/reports/{real_id}/label",
            content="category=capacity_performance&root_cause=OOMKilled+limit&labeled_by=ali",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200 and "Etiket kaydedildi" in resp.text
        md = (await c.get(f"/reports/{real_id}.md")).text
        assert "**Kategori:** capacity_performance" in md and "ali" in md
        bad = await c.post(
            f"/reports/{real_id}/label",
            content="category=hacked&root_cause=xyz&labeled_by=a",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert bad.status_code == 422
        assert (await c.get("/reports/yok/label")).status_code == 404


async def test_label_page_escapes_untrusted_alert_name() -> None:
    store = FakeStore()
    r = make(store)
    r.submit(alert("fp1", name="<script>alert(1)</script>"))
    await r.wait_idle()
    rid = next(iter(store.rows))
    async with await client(r) as c:
        page = (await c.get(f"/reports/{rid}/label")).text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


async def test_report_found_in_store_after_restart() -> None:
    store = FakeStore()
    first = make(store)
    first.submit(alert("fp1"))
    await first.wait_idle()
    rid = next(iter(store.rows))
    restarted = make(store)  # bellek boş, depo dolu
    async with await client(restarted) as c:
        assert (await c.get(f"/reports/{rid}")).json()["alert"]["name"] == "PodBroken"
        assert (await c.get(f"/reports/{rid}.md")).text.startswith("# PodBroken")


async def test_schema_retried_until_postgres_ready_and_queue_flushed() -> None:
    store = FakeStore()
    attempts = 0

    async def migrate() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("postgres açılıyor")

    cfg = AppConfig(default_checks=["pod_restarts"])
    r = ReportRunner(
        cfg,
        CheckRegistry(),
        {Capability.K8S: FakeK8s()},
        now=lambda: END,
        store=store,
        migrate=migrate,
    )
    r.submit(alert("fp1"))
    await r.wait_idle()
    assert not store.rows and len(r._outbox) == 2  # şema hazır değil: rapor + olay kuyrukta
    await r.ensure_schema(retry_s=0)
    assert attempts == 3 and r.schema_ready
    assert await r.flush() == 2 and len(store.rows) == 1


async def test_listing_fails_loudly_when_store_down() -> None:
    store = FakeStore()
    r = make(store)
    r.submit(alert("fp1"))
    await r.wait_idle()
    store.down = True
    async with await client(r) as c:
        assert (await c.get("/reports")).status_code == 503  # boş liste değil


async def test_decisions_made_after_report_and_persisted() -> None:
    from sre_agent.decisions import DecisionConfig, Mode, Point
    from sre_agent.decisions.wiring import build_decider

    store = FakeStore()
    decider = build_decider(DecisionConfig(mode=Mode.SHADOW, models_file="/yok/models.yaml"))
    assert decider is not None  # katalog yok: yalnız kurallar
    cfg = AppConfig(default_checks=["pod_restarts"])
    r = ReportRunner(
        cfg,
        CheckRegistry(),
        {Capability.K8S: FakeK8s()},
        now=lambda: END,
        store=store,
        decider=decider,
    )
    r.submit(alert("fp9"))
    await r.wait_idle()
    (rid,) = store.rows
    dset = store.decisions[rid]
    assert dset.effective(Point.KNOWN) is not None
    assert all(d.source != "model" for d in dset.decisions)
    assert r.decisions[rid] is dset
    assert build_decider(DecisionConfig(mode=Mode.OFF)) is None


async def test_decisions_in_report_and_only_after_labeling() -> None:
    from sre_agent.decisions import DecisionConfig, Mode
    from sre_agent.decisions.wiring import build_decider

    store = FakeStore()
    r = ReportRunner(
        AppConfig(default_checks=["pod_restarts"]),
        CheckRegistry(),
        {Capability.K8S: FakeK8s()},
        now=lambda: END,
        store=store,
        decider=build_decider(DecisionConfig(mode=Mode.SHADOW, models_file="/yok")),
    )
    r.submit(alert("fp1"))
    await r.wait_idle()
    rid = next(iter(store.rows))
    restarted = make(store)  # bellek boş: kararlar depodan okunur
    for runner in (r, restarted):
        async with await client(runner) as c:
            md = (await c.get(f"/reports/{rid}.md")).text
            assert "## Kararlar (mod: shadow)" in md and "| Bilinen olay | benzerlik |" in md
            assert (await c.get(f"/reports/{rid}")).json()["decisions"]
    async with await client(r) as c:
        form = (await c.get(f"/reports/{rid}/label")).text
        assert "Sistemin kararları" not in form  # etiketten önce gösterilmez
        saved = (
            await c.post(
                f"/reports/{rid}/label",
                content="category=capacity_performance&root_cause=OOM&labeled_by=ali",
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        ).text
        assert "Etiket kaydedildi" in saved and "Sistemin kararları" in saved


async def test_reports_grouped_into_incidents_and_labeled_together() -> None:
    from sre_agent.store import Label
    from test_service import restarted_pod

    store = FakeStore()
    k8s = FakeK8s({"Pod": [restarted_pod()]})

    def make(store: FakeStore) -> ReportRunner:  # yeniden başlayan pod: pod_restarts anomali
        return ReportRunner(
            AppConfig(default_checks=["pod_restarts"]),
            CheckRegistry(),
            {Capability.K8S: k8s},
            now=lambda: END,
            store=store,
        )

    r = make(store)
    # aynı namespace, aynı kanıt (pod_restarts), 30 dk içinde: tek olay
    r.submit(alert("fp1", name="PodBroken"))
    await r.wait_idle()
    r.submit(alert("fp2", name="PodCrash"))
    await r.wait_idle()
    reps = list(r.reports.values())
    assert len(reps) == 2 and reps[0].incident_id == reps[1].incident_id
    assert reps[0].incident_reason == "yeni olay" and "aynı namespace" in (
        reps[1].incident_reason or ""
    )
    inc = await store.get_incident(reps[0].incident_id or "")
    assert inc and inc["report_count"] == 2
    n = await store.label_incident(
        inc["id"],
        Label(category="hardware_problem", root_cause="node kaybı", labeled_by="ali"),
        END,
    )
    assert n == 2 and all(
        row["label_category"] == "hardware_problem" for row in store.rows.values()
    )
    # yeniden başlatma: açık olay depodan kurulur, yeni rapor aynı olaya katılır
    restarted = make(store)
    restarted.submit(alert("fp3", name="PodBroken2"))
    await restarted.wait_idle()
    (new,) = restarted.reports.values()
    assert new.incident_id == reps[0].incident_id


async def test_incident_page_and_label_via_api() -> None:
    from test_service import restarted_pod

    store = FakeStore()
    k8s = FakeK8s({"Pod": [restarted_pod()]})
    r = ReportRunner(
        AppConfig(default_checks=["pod_restarts"]),
        CheckRegistry(),
        {Capability.K8S: k8s},
        now=lambda: END,
        store=store,
    )
    r.submit(alert("fp1", name="A1"))
    r.submit(alert("fp2", name="A2"))
    await r.wait_idle()
    (inc_id,) = {x.incident_id for x in r.reports.values()}
    rid = next(iter(r.reports))
    async with await client(r) as c:
        assert f"/incidents/{inc_id}" in (await c.get(f"/reports/{rid}.md")).text
        form = (await c.get(f"/incidents/{inc_id}/label")).text
        assert f'action="/incidents/{inc_id}/label"' in form and "2 rapor" in form
        saved = await c.post(
            f"/incidents/{inc_id}/label",
            content="category=hardware_problem&root_cause=node%20kaybi&labeled_by=ali",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert "Olay etiketlendi (2 rapor)" in saved.text
        assert (await c.get("/incidents/yok")).status_code == 404
    assert {row["label_category"] for row in store.rows.values()} == {"hardware_problem"}


async def test_find_prefers_incident_column_over_stored_json() -> None:
    store = FakeStore()
    r = make(store)
    r.submit(alert("fp1"))
    await r.wait_idle()
    rid = next(iter(store.rows))
    store.rows[rid].update(incident_id="geriye-donuk", incident_reason="geriye dönük")
    found = await make(store).find(rid)  # bellek boş: depodan
    assert found and found.incident_id == "geriye-donuk"


async def test_incident_investigated_once_after_quiet_period() -> None:
    from sre_agent.graph.investigator import GraphMode, InvestigationConfig, Investigator
    from sre_agent.graph.wiring import NoModel
    from test_service import restarted_pod

    store = FakeStore()
    inv = Investigator(
        InvestigationConfig(mode=GraphMode.SHADOW, quiet_s=0.05),
        store,
        lambda _b: NoModel(),  # type: ignore[arg-type,return-value]
    )
    r = ReportRunner(
        AppConfig(default_checks=["pod_restarts"]),
        CheckRegistry(),
        {Capability.K8S: FakeK8s({"Pod": [restarted_pod()]})},
        now=lambda: END,
        store=store,
        investigator=inv,
    )
    r.submit(alert("fp1", name="A1"))
    r.submit(alert("fp2", name="A2"))  # aynı incident: sayaç sıfırlanır, tek koşu
    await r.wait_idle()
    await asyncio.sleep(0.2)
    (inc_id,) = {x.incident_id for x in r.reports.values()}
    assert inc_id is not None
    result = store.investigations[inc_id]
    assert result["mode"] == "shadow" and result["report"]["source"] == "fallback"
    assert result["specialists"] and result["errors"]  # model yok: yedek yol, hata kaydı
    assert len(store.investigations) == 1


INVESTIGATION = {
    "report": {
        "summary": "etcd çoğunluğu kaybedildi",
        "category": "database",
        "root_cause": "3 etcd üyesinden 2'sine ulaşılamadı",
        "evidence_chain": ["milvus_etcd: 2/3 üye hazır değil"],
        "rejected": [{"category": "hardware_problem", "reason": "node kaybı yok"}],
        "recommendations": ["etcd üyelerini ayrı node'lara yay"],
        "certain": True,
        "source": "model",
    },
    "evaluation": {"confidence": "high", "cross_validated": True},
    "findings": {
        "k8s": {
            "specialist": "k8s",
            "confidence": "high",
            "tools_used": ["placement"],
            "summary": "a|b",
        }
    },
    "budget": {"used": 123456},
}


@pytest.mark.parametrize("mode", ["on", "shadow"])
async def test_investigation_section_in_report(mode: str) -> None:
    store = FakeStore()
    r = make(store)
    r.submit(alert("fp1"))
    await r.wait_idle()
    rep = next(iter(r.reports.values()))
    assert rep.incident_id
    await store.save_investigation(rep.incident_id, INVESTIGATION, mode, END)
    async with await client(r) as c:
        md = (await c.get(f"/reports/{rep.id}.md")).text
    section = md.index("## Soruşturma (agent")
    assert "Veritabanı" in md[section:] or "`database`" in md[section:]
    assert "`hardware_problem`: node kaybı yok" in md and "| k8s | yüksek | placement | a/b |" in md
    assert "Token: 123.456" in md
    first_check = md.index("\n## ", md.index("# PodBroken"))
    if mode == "on":
        assert section == first_check + 1 and "gölge" not in md[section : section + 60]
    else:
        assert section > first_check and "gölge" in md[section : section + 60]
