"""Alarm → kontrol seçimi → paralel koşu → rapor → Grafana anotasyonu."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol

from sre_agent import incidents
from sre_agent.checks import CheckContext, Window, run_checks
from sre_agent.checks.builtin import BUILTIN_CHECKS
from sre_agent.config import AppConfig
from sre_agent.decisions import DecisionSet
from sre_agent.freshness import assess, blind_spot_reasons
from sre_agent.incidents import Incident
from sre_agent.llm.budget import PriorityGate, alert_priority
from sre_agent.packs import CheckRegistry
from sre_agent.ports import Capability
from sre_agent.report import AlertRef, Report, annotation_text, build_report, summary_log
from sre_agent.service import metrics
from sre_agent.store import ReportStore, Versions
from sre_agent.taxonomy import load as load_taxonomy
from sre_agent.wiring import build_checks

log = logging.getLogger("sre_agent.runner")

# kube-prometheus-stack'in sürekli açık "hat canlı" alarmı
WATCHDOG = "Watchdog"


class Annotator(Protocol):
    async def annotate(self, at: datetime, text: str, tags: list[str]) -> int: ...


def _parse_time(value: str | None, fallback: datetime) -> datetime:
    if not value or value.startswith("0001-"):
        return fallback
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ReportRunner:
    def __init__(
        self,
        cfg: AppConfig,
        registry: CheckRegistry,
        ports: dict[Capability, Any],
        annotator: Annotator | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        store: ReportStore | None = None,
        migrate: Callable[[], Awaitable[None]] | None = None,
        decider: Callable[[Report, list[dict[str, Any]]], Awaitable[DecisionSet]] | None = None,
        investigator: Any = None,
    ):
        self.cfg = cfg
        self.registry = registry
        self.ports = ports
        self.annotator = annotator
        self.now = now
        self.reports: OrderedDict[str, Report] = OrderedDict()
        self._seen: dict[str, datetime] = {}
        # Aynı anda en fazla N koşu; bekleyenlerde critical önce (ADR-004)
        self._gate = PriorityGate(cfg.max_concurrent_runs, metrics.RUNS_WAITING.set)
        self._tasks: set[asyncio.Task[Report | None]] = set()
        self.store = store
        self.versions = Versions(
            app=_app_version(),
            taxonomy=load_taxonomy().version,
            packs={p.manifest.name: p.manifest.version for p in registry.packs},
        )
        # Postgres'e yazılamayan kayıtlar (rapor ya da çözülme); flush() dakikada bir dener
        self._outbox: list[tuple[str, Any]] = []
        self._migrate = migrate
        # Tazelik sorguları: ortama özgü (yapılandırma) + ürüne özgü (paketler)
        self.probes = [*cfg.freshness, *registry.freshness]
        self.schema_ready = migrate is None
        # Olay gruplama (T3.1): açık olaylar bellekte; ilk kullanımda son WINDOW'un raporlarından
        self._incidents: list[Incident] = []
        self._incidents_loaded = store is None
        self._incident_lock = asyncio.Lock()
        # Karar katmanı (T2.5): rapor yazıldıktan sonra ayrı görevde; rapor süresini etkilemez
        self.decider = decider
        # Investigation graph (T3.2): incident'a rapor gelince sessizlik sayacı başlar
        self.investigator = investigator
        self.decisions: dict[str, DecisionSet] = {}

    def select(self, alertname: str) -> list[str]:
        routed = self.registry.for_alert(alertname)
        if routed:
            return routed
        return list(self.cfg.default_checks) or sorted(c.name for c in BUILTIN_CHECKS)

    def submit(self, payload: dict[str, Any]) -> list[str]:
        """Webhook gövdesindeki firing alarmları arka planda koşar; kabul edilen fingerprint'ler."""
        accepted: list[str] = []
        now = self.now()
        for alert in payload.get("alerts") or []:
            status = alert.get("status") or "unknown"
            name = (alert.get("labels") or {}).get("alertname")
            if name == WATCHDOG:  # hat canlılığı: rapor üretilmez, yalnız zamanı kaydedilir
                metrics.WATCHDOG_LAST_SEEN.set(now.timestamp())
                continue
            metrics.ALERTS.labels(status=status).inc()
            if status == "resolved" and alert.get("fingerprint"):
                ends = _parse_time(alert.get("endsAt"), now)
                self._spawn(self._persist(("resolve", (alert["fingerprint"], ends))))
            if status != "firing":
                continue
            fp = alert.get("fingerprint") or json.dumps(alert.get("labels", {}), sort_keys=True)
            last = self._seen.get(fp)
            if last and now - last < timedelta(minutes=self.cfg.dedupe_min):
                metrics.DEDUPED.inc()
                continue
            self._seen[fp] = now
            self._spawn(self._run_guarded(alert, fp))
            accepted.append(fp)
        return accepted

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _run_guarded(self, alert: dict[str, Any], fp: str) -> Report | None:
        async with self._gate.slot(alert_priority(alert)):
            metrics.IN_PROGRESS.inc()
            try:
                return await self.run(alert, fp)
            except Exception:
                log.exception("rapor üretilemedi: %s", fp)
                metrics.REPORTS.labels(result="failed", blind_spot="unknown").inc()
                return None
            finally:
                metrics.IN_PROGRESS.dec()

    async def run(self, alert: dict[str, Any], fp: str) -> Report:
        started = time.perf_counter()
        labels: dict[str, str] = alert.get("labels") or {}
        name = labels.get("alertname", "?")
        now = self.now()
        # Gelecekteki startsAt (ör. yalnızca endsAt ile gönderilmiş alarm) şimdiye çekilir.
        starts_at = min(_parse_time(alert.get("startsAt"), now), now)
        window = Window(start=starts_at - timedelta(minutes=self.cfg.window_before_min), end=now)
        checks = build_checks(self.select(name), self.registry, self.cfg)
        ctx = CheckContext(labels=labels, window=window, ports=self.ports)
        needed = {c.capability for c in checks}
        evidence, sources = await asyncio.gather(
            run_checks(checks, ctx),
            assess(self.ports, needed, self.probes, labels, now),
        )
        for e in evidence:
            metrics.CHECK_RESULTS.labels(check=e.check, status=e.status).inc()
            metrics.CHECK_SECONDS.labels(check=e.check).observe(e.duration_ms / 1000)
        blind = blind_spot_reasons(sources, [str(e.status) for e in evidence])
        report = build_report(
            report_id=uuid.uuid4().hex[:12],
            alert=AlertRef(name=name, fingerprint=fp, labels=labels, starts_at=starts_at),
            window=window,
            generated_at=self.now(),
            duration_ms=(time.perf_counter() - started) * 1000,
            evidence=evidence,
            sources=sources,
            blind_spots=blind,
            packs=self.registry.packs_for_alert(name),
        )
        if self.annotator:
            try:
                tags = [
                    "sre-agent",
                    name,
                    *(f"{k}={labels[k]}" for k in ("namespace",) if k in labels),
                ]
                if labels.get("test") == "true":  # sentetik alarmlar listede ayırt edilsin
                    tags.append("test")
                annotation_id = await self.annotator.annotate(
                    starts_at, annotation_text(report, self.cfg.report_base_url), tags
                )
                report = report.model_copy(update={"annotation_id": annotation_id})
            except Exception:
                log.exception("Grafana anotasyonu yazılamadı: %s", report.id)
                metrics.SINK_ERRORS.labels(sink="grafana").inc()
        report = await self._assign_incident(report)
        self._store(report)
        await self._persist(("save", report))
        inc = next(i for i in self._incidents if i.id == report.incident_id)
        await self._persist(("incident", inc))
        if self.investigator is not None and report.incident_id:
            self.investigator.schedule(report.incident_id)
        if self.decider is not None:
            self._spawn(self._decide(report))
        metrics.REPORTS.labels(result="ok", blind_spot=str(bool(blind)).lower()).inc()
        metrics.REPORT_SECONDS.observe(time.perf_counter() - started)
        print(json.dumps(summary_log(report), ensure_ascii=False), flush=True)
        return report

    async def _load_incidents(self) -> None:
        """Yeniden başlatmadan sonra açık olayları depodaki son raporlardan kurar."""
        if self._incidents_loaded or self.store is None or not self.schema_ready:
            return
        try:
            rows = await self.store.recent_reports(self.now() - incidents.WINDOW)
        except Exception:
            log.exception("açık olaylar okunamadı; boş başlanıyor")
            rows = []
        by_id: dict[str, Incident] = {}
        for row in rows:
            if not row.get("incident_id"):
                continue
            sig = incidents.Signature.of(Report.model_validate(row["report"]))
            by_id.setdefault(row["incident_id"], Incident(id=row["incident_id"])).members.append(
                sig
            )
        self._incidents = list(by_id.values())
        self._incidents_loaded = True

    async def _assign_incident(self, report: Report) -> Report:
        async with self._incident_lock:
            await self._load_incidents()
            now = self.now()
            self._incidents = [i for i in self._incidents if now - i.last_seen <= incidents.WINDOW]
            inc, why = incidents.assign(incidents.Signature.of(report), self._incidents)
        metrics.INCIDENT_ASSIGNMENTS.labels(kind="yeni" if why == "yeni olay" else "katıldı").inc()
        return report.model_copy(update={"incident_id": inc.id, "incident_reason": why})

    async def find_decisions(self, report_id: str) -> list[dict[str, Any]]:
        """Raporun kararları: bellekte varsa oradan, yoksa depodan (aynı alan adlarıyla)."""
        dset = self.decisions.get(report_id)
        if dset is not None:
            return [
                {**d.model_dump(mode="json"), "mode": str(dset.mode), "noise_flag": dset.noise_flag}
                for d in dset.decisions
            ]
        if self.store is not None and self.schema_ready:
            try:
                return await self.store.get_decisions(report_id)
            except Exception:
                log.exception("kararlar okunamadı: %s", report_id)
        return []

    async def _decide(self, report: Report) -> None:
        assert self.decider is not None
        try:
            history: list[dict[str, Any]] = []
            if self.store is not None and self.schema_ready:
                history = await self.store.labeled_history(report.alert.name)
            dset = await self.decider(report, history)
        except Exception:
            log.exception("karar üretilemedi: %s", report.id)
            metrics.DECISIONS.labels(point="all", source="error", outcome="error").inc()
            return
        self.decisions[report.id] = dset
        while len(self.decisions) > self.cfg.keep_reports:
            self.decisions.pop(next(iter(self.decisions)))
        for d in dset.decisions:
            metrics.DECISIONS.labels(
                point=str(d.point),
                source=d.source if d.effective else f"{d.source}-gölge",
                outcome=d.outcome,
            ).inc()
        await self._persist(("decisions", (report.id, dset, self.now())))

    def _store(self, report: Report) -> None:
        self.reports[report.id] = report
        while len(self.reports) > self.cfg.keep_reports:
            self.reports.popitem(last=False)

    # ---------------------------------------------------------------- kalıcı depo

    async def _persist(self, item: tuple[str, Any]) -> None:
        if self.store is None:
            return
        if not self.schema_ready:  # şema kurulana kadar kuyrukta bekler
            self._outbox.append(item)
            return
        try:
            await self._apply(item)
        except Exception:
            log.exception("Postgres'e yazılamadı, kuyruğa alındı: %s", item[0])
            metrics.SINK_ERRORS.labels(sink="db").inc()
            self._outbox.append(item)

    async def _apply(self, item: tuple[str, Any]) -> None:
        assert self.store is not None
        kind, value = item
        if kind == "save":
            await self.store.save(value, self.versions)
        elif kind == "resolve":
            fingerprint, at = value
            await self.store.resolve(fingerprint, at)
        elif kind == "incident":
            await self.store.save_incident(value)
        elif kind == "decisions":
            report_id, dset, at = value
            await self.store.save_decisions(report_id, dset, at)

    async def flush(self) -> int:
        """Kuyruktaki kayıtları yeniden yazar; yazılamayan kalır. Yazılan sayı."""
        pending, self._outbox = self._outbox, []
        done = 0
        for i, item in enumerate(pending):
            try:
                await self._apply(item)
                done += 1
            except Exception:
                self._outbox.extend(pending[i:])
                break
        return done

    async def ensure_schema(self, retry_s: float = 10) -> None:
        """Migration'ı Postgres hazır olana kadar dener; bu sırada yazılar kuyrukta bekler."""
        while not self.schema_ready and self._migrate is not None:
            try:
                await self._migrate()
                self.schema_ready = True
                log.info("rapor deposu şeması hazır")
            except Exception as exc:
                log.warning("Postgres henüz hazır değil, %s sn sonra tekrar: %s", retry_s, exc)
                metrics.SINK_ERRORS.labels(sink="db").inc()
                await asyncio.sleep(retry_s)

    async def maintain(self, interval_s: float = 60, purge_every: int = 360) -> None:
        """Arka plan: şema, dakikada bir kuyruk, 6 saatte bir saklama (etiketliler korunur)."""
        await self.ensure_schema()
        await self.flush()
        tick = 0
        while True:
            await asyncio.sleep(interval_s)
            await self.flush()
            tick += 1
            if self.store is not None and tick % purge_every == 0:
                cutoff = self.now() - timedelta(days=self.cfg.retention_days)
                try:
                    removed = await self.store.purge(cutoff)
                    log.info("saklama: %d eski etiketsiz rapor silindi", removed)
                except Exception:
                    log.exception("saklama uygulanamadı")

    async def find(self, report_id: str) -> Report | None:
        """Önce bellek, sonra Postgres."""
        if report_id in self.reports:
            return self.reports[report_id]
        if self.store is None:
            return None
        row = await self.store.get(report_id)
        if not row:
            return None
        # Olay sütunu rapor JSON'undan sonra da yazılabilir (geriye dönük gruplama): sütun kazanır
        extra = {k: row[k] for k in ("incident_id", "incident_reason") if row.get(k)}
        return Report.model_validate(row["report"] | extra)


def _app_version() -> str:
    try:
        return version("sre-agent")
    except PackageNotFoundError:
        return "0.0.0"
