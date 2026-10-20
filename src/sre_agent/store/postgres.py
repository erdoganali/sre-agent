"""ReportStore'un Postgres uygulaması (SQLAlchemy async + asyncpg)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Identity,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    delete,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from sre_agent.decisions import DecisionSet
from sre_agent.incidents import Incident
from sre_agent.report import Report
from sre_agent.store import Label, Versions

SCHEMA = "reports"
metadata = MetaData(schema=SCHEMA)

reports = Table(
    "reports",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("fingerprint", String(200), nullable=False, index=True),
    Column("alertname", String(200), nullable=False, index=True),
    Column("namespace", String(200), index=True),
    Column("severity", String(50)),
    Column("labels", JSONB, nullable=False),
    Column("starts_at", DateTime(timezone=True), nullable=False),
    Column("generated_at", DateTime(timezone=True), nullable=False, index=True),
    Column("resolved_at", DateTime(timezone=True)),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("window_end", DateTime(timezone=True), nullable=False),
    Column("duration_ms", Float, nullable=False),
    Column("anomalies", ARRAY(Text), nullable=False),
    Column("anomalies_by_category", JSONB, nullable=False),
    Column("blind_spots", ARRAY(Text), nullable=False),
    Column("packs", ARRAY(Text), nullable=False, server_default="{}"),
    Column("is_test", Boolean, nullable=False, index=True),
    Column("annotation_id", Integer),
    Column("app_version", String(50), nullable=False),
    Column("taxonomy_version", String(20), nullable=False),
    Column("pack_versions", JSONB, nullable=False),
    Column("report", JSONB, nullable=False),  # raporun tamamı
    Column("label_category", String(64), index=True),
    Column("label_root_cause", Text),
    Column("labeled_by", String(100)),
    Column("labeled_at", DateTime(timezone=True)),
    Column("incident_id", String(32), index=True),
    Column("incident_reason", Text),
)

incidents = Table(
    "incidents",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("opened_at", DateTime(timezone=True), nullable=False, index=True),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("team", String(100)),
    Column("namespaces", ARRAY(Text), nullable=False),
    Column("lost_nodes", ARRAY(Text), nullable=False),
    Column("common_anomalies", ARRAY(Text), nullable=False),
    Column("report_count", Integer, nullable=False),
    Column("label_category", String(64)),
    Column("label_root_cause", Text),
    Column("labeled_by", String(100)),
    Column("labeled_at", DateTime(timezone=True)),
    Column("investigation", JSONB),
    Column("investigated_at", DateTime(timezone=True)),
    Column("investigation_mode", String(10)),
)

decisions = Table(
    "decisions",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("report_id", String(32), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("point", String(20), nullable=False, index=True),
    Column("source", String(20), nullable=False),
    Column("effective", Boolean, nullable=False),
    Column("mode", String(10), nullable=False),
    Column("outcome", String(64), nullable=False),
    Column("probability", Float),
    Column("margin", Float),
    Column("probs", JSONB, nullable=False),
    Column("thresholds", JSONB, nullable=False),
    Column("method", String(20)),
    Column("model", String(100)),
    Column("taxonomy_version", String(20), nullable=False),
    Column("input_hash", String(32), nullable=False),
    Column("ref_report", String(32)),
    Column("reason", Text),
    Column("error", Text),
    Column("noise_flag", Boolean, nullable=False),
)

SUMMARY = [
    c for c in reports.c if c.name not in ("report", "pack_versions", "anomalies_by_category")
]


def async_url(url: str) -> str:
    """postgresql://… → postgresql+asyncpg://…"""
    return (
        url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if "+" not in url.split("://")[0]
        else url
    )


class PostgresReportStore:
    def __init__(self, url: str, engine: AsyncEngine | None = None):
        self.engine = engine or create_async_engine(async_url(url), pool_pre_ping=True, pool_size=5)

    async def save(self, report: Report, versions: Versions) -> None:
        a = report.alert
        row = {
            "id": report.id,
            "fingerprint": a.fingerprint,
            "alertname": a.name,
            "namespace": a.labels.get("namespace"),
            "severity": a.labels.get("severity"),
            "labels": a.labels,
            "starts_at": a.starts_at,
            "generated_at": report.generated_at,
            "window_start": report.window.start,
            "window_end": report.window.end,
            "duration_ms": report.duration_ms,
            "anomalies": [e.check for e in report.anomalies],
            "anomalies_by_category": report.anomalies_by_category,
            "blind_spots": list(report.blind_spots),
            "packs": list(report.packs),
            "is_test": a.labels.get("test") == "true",
            "annotation_id": report.annotation_id,
            "app_version": versions.app,
            "taxonomy_version": versions.taxonomy,
            "pack_versions": versions.packs,
            "report": report.model_dump(mode="json"),
            "incident_id": report.incident_id,
            "incident_reason": report.incident_reason,
        }
        stmt = insert(reports).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=[reports.c.id],
            set_={k: stmt.excluded[k] for k in row if k != "id"},
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    async def resolve(self, fingerprint: str, at: datetime) -> int:
        stmt = (
            update(reports)
            .where(and_(reports.c.fingerprint == fingerprint, reports.c.resolved_at.is_(None)))
            .values(resolved_at=at)
        )
        async with self.engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount

    async def get(self, report_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(reports).where(reports.c.id == report_id))).first()
        return dict(row._mapping) if row else None

    async def recent(self, limit: int, include_test: bool) -> list[dict[str, Any]]:
        stmt = select(*SUMMARY).order_by(reports.c.generated_at.desc()).limit(limit)
        if not include_test:
            stmt = stmt.where(reports.c.is_test.is_(False))
        async with self.engine.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(stmt)]

    async def set_label(self, report_id: str, label: Label, at: datetime) -> bool:
        stmt = (
            update(reports)
            .where(reports.c.id == report_id)
            .values(
                label_category=label.category,
                label_root_cause=label.root_cause,
                labeled_by=label.labeled_by,
                labeled_at=at,
            )
        )
        async with self.engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount == 1

    async def save_decisions(self, report_id: str, dset: DecisionSet, at: datetime) -> None:
        rows = [
            {
                "report_id": report_id,
                "created_at": at,
                "point": str(d.point),
                "source": d.source,
                "effective": d.effective,
                "mode": str(dset.mode),
                "outcome": d.outcome,
                "probability": d.probability,
                "margin": d.margin,
                "probs": d.probs,
                "thresholds": dset.thresholds.model_dump(),
                "method": d.method,
                "model": d.model,
                "taxonomy_version": dset.taxonomy_version,
                "input_hash": dset.input_hash,
                "ref_report": d.ref_report,
                "reason": d.reason or None,
                "error": d.error,
                "noise_flag": dset.noise_flag,
            }
            for d in dset.decisions
        ]
        async with self.engine.begin() as conn:
            await conn.execute(delete(decisions).where(decisions.c.report_id == report_id))
            if rows:
                await conn.execute(insert(decisions), rows)

    async def save_incident(self, inc: Incident) -> None:
        row = {
            "id": inc.id,
            "opened_at": inc.opened_at,
            "last_seen_at": inc.last_seen,
            "team": inc.members[0].team,
            "namespaces": inc.namespaces,
            "lost_nodes": inc.lost_nodes,
            "common_anomalies": inc.common_anomalies,
            "report_count": len(inc.members),
        }
        stmt = insert(incidents).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=[incidents.c.id], set_={k: stmt.excluded[k] for k in row if k != "id"}
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    async def set_report_incident(self, report_id: str, incident_id: str, why: str) -> None:
        stmt = (
            update(reports)
            .where(reports.c.id == report_id)
            .values(incident_id=incident_id, incident_reason=why)
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    async def recent_reports(self, since: datetime) -> list[dict[str, Any]]:
        """Gruplama için: verilen andan sonraki raporlar (tam rapor ve olay kimliği)."""
        stmt = (
            select(reports.c.id, reports.c.report, reports.c.incident_id)
            .where(reports.c.generated_at >= since)
            .order_by(reports.c.generated_at)
        )
        async with self.engine.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(stmt)]

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(select(incidents).where(incidents.c.id == incident_id))
            ).first()
            if row is None:
                return None
            members = await conn.execute(
                select(*SUMMARY)
                .where(reports.c.incident_id == incident_id)
                .order_by(reports.c.generated_at)
            )
            return dict(row._mapping) | {"reports": [dict(m._mapping) for m in members]}

    async def label_incident(self, incident_id: str, label: Label, at: datetime) -> int:
        """Olayın etiketini ve bütün üye raporlarının etiketini yazar; güncellenen rapor sayısı."""
        by = f"{label.labeled_by} (olay {incident_id})"[:100]
        async with self.engine.begin() as conn:
            await conn.execute(
                update(incidents)
                .where(incidents.c.id == incident_id)
                .values(
                    label_category=label.category,
                    label_root_cause=label.root_cause,
                    labeled_by=label.labeled_by,
                    labeled_at=at,
                )
            )
            res = await conn.execute(
                update(reports)
                .where(reports.c.incident_id == incident_id)
                .values(
                    label_category=label.category,
                    label_root_cause=label.root_cause,
                    labeled_by=by,
                    labeled_at=at,
                )
            )
            return res.rowcount

    async def incident_reports(self, incident_id: str) -> list[dict[str, Any]]:
        """Incident'ın raporlarının tam JSON'u (olay sütunu dahil), zaman sırasıyla."""
        stmt = (
            select(reports.c.report, reports.c.incident_id)
            .where(reports.c.incident_id == incident_id)
            .order_by(reports.c.generated_at)
        )
        async with self.engine.connect() as conn:
            return [
                dict(r.report) | {"incident_id": r.incident_id} for r in await conn.execute(stmt)
            ]

    async def save_investigation(
        self, incident_id: str, result: dict[str, Any], mode: str, at: datetime
    ) -> None:
        stmt = (
            update(incidents)
            .where(incidents.c.id == incident_id)
            .values(investigation=result, investigated_at=at, investigation_mode=mode)
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    async def get_decisions(self, report_id: str) -> list[dict[str, Any]]:
        stmt = select(decisions).where(decisions.c.report_id == report_id).order_by(decisions.c.id)
        async with self.engine.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(stmt)]

    async def labeled_history(self, alertname: str, limit: int = 50) -> list[dict[str, Any]]:
        """Aynı alarmın etiketli son raporları (bilinen olay benzerliği için)."""
        stmt = (
            select(
                reports.c.id,
                reports.c.alertname.label("alert"),
                reports.c.anomalies.label("anomaly_checks"),
                reports.c.label_category,
            )
            .where(and_(reports.c.alertname == alertname, reports.c.label_category.is_not(None)))
            .order_by(reports.c.generated_at.desc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(stmt)]

    async def purge(self, older_than: datetime) -> int:
        stmt = delete(reports).where(
            and_(reports.c.generated_at < older_than, reports.c.label_category.is_(None))
        )
        async with self.engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount

    async def aclose(self) -> None:
        await self.engine.dispose()
