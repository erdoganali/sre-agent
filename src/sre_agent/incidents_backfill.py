"""Geçmiş raporları olaylara gruplar (T3.1, geriye dönük).

  python -m sre_agent.incidents_backfill --config /etc/sre-agent/config.yaml --days 30 [--dry-run]

Olay gruplamasından önce yazılmış raporlarda node kaybı kanıtı (node_recreated) yoktur; her
raporun kendi penceresi için Prometheus'tan hesaplanır (Prometheus'un saklama süresi kadar
geriye). Seçilen aralığın bütün raporları baştan gruplanır, olay kimlikleri raporlara yazılır,
üyesi kalmayan olaylar silinir. Pod içinde çalışır (DATABASE_URL pod'da).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from sre_agent import incidents
from sre_agent.adapters.prometheus import PrometheusMetrics
from sre_agent.checks import CheckContext, Status
from sre_agent.checks.builtin import NodeRecreated
from sre_agent.config import load_app_config
from sre_agent.ports import Capability
from sre_agent.report import Report
from sre_agent.store.postgres import PostgresReportStore, reports
from sre_agent.store.postgres import incidents as incidents_table


async def signature(report: Report, prom: PrometheusMetrics) -> incidents.Signature:
    sig = incidents.Signature.of(report)
    if any(e.check == incidents.NODE_CHECK for e in report.evidence):
        return sig
    labels = {"namespace": sig.namespace} if sig.namespace else {}
    ctx = CheckContext(labels=labels, window=report.window, ports={Capability.METRICS: prom})
    ev = await NodeRecreated().run(ctx)
    if ev.status is not Status.ANOMALY or not isinstance(ev.data, list):
        return sig
    lost = frozenset(
        f"{r['node']}@{r['created_at']}" if r.get("created_at") else str(r["node"])
        for r in ev.data
        if isinstance(r, dict)
    )
    return incidents.Signature(
        sig.report_id, sig.at, sig.namespace, sig.anomalies | {incidents.NODE_CHECK}, lost, sig.team
    )


async def run(config: Path, days: int, dry_run: bool) -> int:
    cfg = load_app_config(config)
    url = os.environ.get("DATABASE_URL")
    if not url or not cfg.prometheus_url:
        print("DATABASE_URL ya da prometheus_url yok")
        return 2
    store = PostgresReportStore(url)
    prom = PrometheusMetrics(cfg.prometheus_url)
    since = datetime.now(UTC) - timedelta(days=days)
    stmt = select(reports.c.id, reports.c.report).where(reports.c.generated_at >= since)
    async with store.engine.connect() as conn:
        rows = [dict(r._mapping) for r in await conn.execute(stmt)]
    sigs = [await signature(Report.model_validate(r["report"]), prom) for r in rows]
    result = incidents.group(sigs)
    sizes = Counter(len(i.members) for i in result)
    print(f"{len(rows)} rapor → {len(result)} olay; boyutlar: {sorted(sizes.items())}")
    if dry_run:
        await store.aclose()
        return 0
    for inc in result:
        await store.save_incident(inc)
        for idx, m in enumerate(inc.members):
            why = (
                "yeni olay"
                if idx == 0
                else incidents.reason(m, _prefix(inc, idx)) or "geriye dönük"
            )
            await store.set_report_incident(m.report_id, inc.id, why)  # find() sütunu okur
    async with store.engine.begin() as conn:
        # Bütün üye raporları aynı etiketi taşıyan olay o etiketi alır (karışık olay etiketsiz)
        derived = await conn.exec_driver_sql(
            """
            UPDATE reports.incidents i
               SET label_category = x.lab, label_root_cause = x.root,
                   labeled_by = 'türetildi: üye raporlar', labeled_at = now()
              FROM (SELECT incident_id, min(label_category) AS lab, min(label_root_cause) AS root,
                           count(DISTINCT label_category) AS kinds,
                           count(*) AS n, count(label_category) AS labeled
                      FROM reports.reports WHERE incident_id IS NOT NULL
                     GROUP BY incident_id) x
             WHERE x.incident_id = i.id AND x.kinds = 1 AND x.labeled = x.n
               AND i.label_category IS NULL
            """
        )
        print(f"etiketi üye raporlardan türetilen olay: {derived.rowcount}")
        live = select(reports.c.incident_id).where(reports.c.incident_id.is_not(None))
        gone = await conn.execute(delete(incidents_table).where(incidents_table.c.id.not_in(live)))
    print(f"yazıldı; üyesi kalmayan {gone.rowcount} olay silindi")
    await store.aclose()
    return 0


def _prefix(inc: incidents.Incident, idx: int) -> incidents.Incident:
    """Üyenin katılma nedenini, katıldığı andaki olay üzerinden yeniden hesaplamak için."""
    return incidents.Incident(id=inc.id, members=list(inc.members[:idx]))


def main() -> int:
    parser = argparse.ArgumentParser(prog="sre_agent.incidents_backfill")
    parser.add_argument("--config", type=Path, default=Path("/etc/sre-agent/config.yaml"))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args.config, args.days, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
