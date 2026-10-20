"""Etiketli raporlarda karar ölçümü (T2.4/T2.5/T2.6): kurallar ve model yan yana.

  python -m sre_agent.decisions export > etiketli.json          # DB'den (pod içinde)
  python -m sre_agent.decisions evaluate [--from-file etiketli.json] [--models ...] [--save]
  python -m sre_agent.decisions profile --from-file etiketli.json [--models ...] [--n 30]

DATABASE_URL ortam değişkeninden okunur (pod içinde çalıştırılır; parola ekrana gelmez).
--from-file ile veritabanı gerekmez: model başka bir kümedeyse ölçüm orada koşar.
Sonuçlar olay türü (senaryo / node kaybı / diğer) ve olay başına verilir: aynı olayın onlarca
raporu (ör. bir node kaybı dalgası) sonucu o kadar kat etkilemesin. --save yalnız DB ile.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from sre_agent.decisions import NOT_ASSESSABLE, UNKNOWN, DecisionConfig, Mode, Point, brief
from sre_agent.decisions.wiring import build_decider
from sre_agent.report import Report
from sre_agent.store.postgres import PostgresReportStore, reports
from sre_agent.taxonomy import load as load_taxonomy

VERDICTS = ("doğru", "bilinmiyor", "yanlış")


def _verdict(outcome: str | None, label: str) -> str:
    if outcome in (None, UNKNOWN, NOT_ASSESSABLE):
        return "bilinmiyor"
    return "doğru" if outcome == label else "yanlış"


def kind_of(labeled_by: str | None) -> str:
    by = labeled_by or ""
    if by.startswith("senaryo:"):
        return "senaryo"
    if "node kaybı" in by:
        return "node kaybı"
    return "diğer"


def event_key(row: dict[str, Any]) -> tuple[str, ...]:
    """Olay: olay gruplamasının kimliği (T3.1); yoksa toplu etiketlenmiş node kaybı dalgası saat
    başına tek olay, diğerleri rapor başına."""
    if row.get("incident_id"):
        return ("olay", str(row["incident_id"]))
    if kind_of(row["labeled_by"]) == "node kaybı":
        at = str(row["report"]["generated_at"])[:13]
        return ("node", row["report"]["alert"]["labels"].get("namespace", "?"), at)
    return ("rapor", row["id"])


def one_per_event(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: str(r["report"]["generated_at"])):
        seen.setdefault(event_key(row), row)
    return list(seen.values())


async def load_rows(from_file: Path | None, limit: int) -> list[dict[str, Any]]:
    if from_file:
        rows: list[dict[str, Any]] = json.loads(from_file.read_text())
        return rows[:limit]
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL yok (ya da --from-file ver)")
    store = PostgresReportStore(url)
    stmt = (
        select(
            reports.c.id,
            reports.c.report,
            reports.c.label_category,
            reports.c.labeled_by,
            reports.c.incident_id,
        )
        .where(reports.c.label_category.is_not(None))
        .order_by(reports.c.generated_at)
        .limit(limit)
    )
    async with store.engine.connect() as conn:
        out = [dict(r._mapping) for r in await conn.execute(stmt)]
    await store.aclose()
    return out


def print_table(tally: dict[str, dict[str, dict[str, int]]], unit: str) -> None:
    for src, kinds in tally.items():
        print(f"\n== {src} ({unit} başına)")
        totals: dict[str, int] = defaultdict(int)
        for k, v in sorted(kinds.items()):
            n = sum(v.values())
            print(f"  {k:<12} n={n:<4}" + "".join(f" {x}={v[x]:<4}" for x in VERDICTS))
            for x, c in v.items():
                totals[x] += c
        n = sum(totals.values())
        if n:
            print(
                f"  {'TOPLAM':<12} n={n:<4}"
                + "".join(f" {x} %{100 * totals[x] / n:.0f}" for x in VERDICTS)
            )


async def export() -> int:
    rows = await load_rows(None, 100_000)
    json.dump(rows, sys.stdout, ensure_ascii=False, default=str)
    return 0


async def evaluate(
    models: Path, limit: int, save: bool, from_file: Path | None, per_event: bool
) -> int:
    rows = await load_rows(from_file, limit)
    if per_event:
        rows = one_per_event(rows)
    decider = build_decider(DecisionConfig(mode=Mode.SHADOW, models_file=str(models)))
    assert decider is not None
    store = None
    if save:
        if from_file:
            raise SystemExit("--save yalnız veritabanıyla")
        store = PostgresReportStore(os.environ["DATABASE_URL"])
    tally: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    print(f"{'rapor':<14}{'etiket':<24}{'kural':<24}{'model':<28}tür")
    for row in rows:
        report = Report.model_validate(row["report"])
        dset = await decider(report, [])  # bilinen-olay benzerliği ölçüme karışmasın
        if store:
            await store.save_decisions(report.id, dset, datetime.now(UTC))
        label, kind = row["label_category"], kind_of(row["labeled_by"])
        shown = {}
        for src in ("rules", "model"):
            cat = next(
                (d for d in dset.decisions if d.source == src and d.point is Point.CATEGORY), None
            )
            grp = next(
                (d for d in dset.decisions if d.source == src and d.point is Point.GROUP), None
            )
            outcome = cat.outcome if cat else (grp.outcome if grp else None)
            if src == "model" and grp and grp.error:
                shown[src] = "hata"
                continue
            if cat and cat.probability is not None:
                shown[src] = f"{outcome} {cat.probability:.2f}"
            else:
                shown[src] = outcome or "-"
            if src == "rules" or grp:
                tally[src][kind][_verdict(outcome, label)] += 1
        print(f"{row['id']!s:<14}{label:<24}{shown['rules']:<24}{shown['model']:<28}{kind}")
    if store:
        await store.aclose()
        print(f"{len(rows)} raporun kararları kaydedildi")
    print_table(tally, "olay" if per_event else "rapor")
    return 0


async def profile(models: Path, from_file: Path, n: int, out: Path | None) -> int:
    """T2.4: tek tipli soru ("kök neden kategorisi?") düşünme kapalı ve açık; doğruluk, şema
    uyumu, süre, token. Olay başına bir rapor, en çok n olay; türler dengeli seçilir."""
    from sre_agent.adapters.openai_compat import OpenAICompatModel
    from sre_agent.llm.budget import Budget, BudgetedModel
    from sre_agent.llm.catalog import load_catalog
    from sre_agent.llm.telemetry import InstrumentedModel
    from sre_agent.ports.model import ModelError

    taxonomy = load_taxonomy()
    cats = [c.id for c in taxonomy.categories]
    rows = one_per_event(await load_rows(from_file, 100_000))
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_kind[kind_of(r["labeled_by"])].append(r)
    picked: list[dict[str, Any]] = []
    while len(picked) < n and any(by_kind.values()):
        for k in sorted(by_kind):
            if by_kind[k] and len(picked) < n:
                picked.append(by_kind[k].pop(0))

    class Answer(BaseModel):
        category: str
        reason: str

    guide = "\n".join(f"- {c.id}: {c.definition}" for c in taxonomy.categories)
    catalog = load_catalog(models)
    adapter = OpenAICompatModel(catalog)
    settings = {"düşünme kapalı": ("decision", "none"), "düşünme açık": ("synthesize", "medium")}
    results: list[dict[str, Any]] = []
    try:
        for name, (role, effort) in settings.items():
            for r in picked:
                rep = Report.model_validate(r["report"])
                s = brief.summary(rep)
                q = (
                    "Kök nedenin kategorisi hangisi? Yalnız şu kimliklerden birini seç "
                    f"({', '.join(cats)}). Tanımlar:\n{guide}"
                )
                model = InstrumentedModel(BudgetedModel(adapter, catalog, Budget()), catalog)
                t0 = time.monotonic()
                row: dict[str, Any] = {
                    "ayar": name,
                    "rapor": r["id"],
                    "tür": kind_of(r["labeled_by"]),
                    "etiket": r["label_category"],
                }
                try:
                    t = await model.complete_typed(
                        role,
                        brief.messages(s, q),
                        Answer,
                        max_tokens=4096,
                        reasoning_effort=effort,
                    )
                    row |= {
                        "cevap": t.value.category,
                        "sonuç": _verdict(
                            t.value.category if t.value.category in cats else None,
                            r["label_category"],
                        ),
                        "düzeltme": t.repaired,
                        "token": t.completion.usage.total,
                    }
                except ModelError as exc:
                    row |= {"cevap": None, "sonuç": "hata", "hata": str(exc)[:120]}
                row["süre_s"] = round(time.monotonic() - t0, 1)
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        await adapter.aclose()
    print("\n== profil (olay başına)")
    for name in settings:
        rs = [x for x in results if x["ayar"] == name]
        ok = [x for x in rs if x["sonuç"] != "hata"]
        tok = sorted(x["token"] for x in ok) or [0]
        sec = sorted(x["süre_s"] for x in ok) or [0.0]
        print(
            f"  {name:<15} n={len(rs)} doğru={sum(x['sonuç'] == 'doğru' for x in rs)}"
            f" bilinmiyor={sum(x['sonuç'] == 'bilinmiyor' for x in rs)}"
            f" yanlış={sum(x['sonuç'] == 'yanlış' for x in rs)} hata={len(rs) - len(ok)}"
            f" düzeltme={sum(bool(x.get('düzeltme')) for x in ok)}"
            f" token(medyan)={tok[len(tok) // 2]} süre(medyan)={sec[len(sec) // 2]}s"
        )
        for k in sorted({x["tür"] for x in rs}):
            kr = [x for x in rs if x["tür"] == k]
            print(f"      {k:<12} {sum(x['sonuç'] == 'doğru' for x in kr)}/{len(kr)} doğru")
    if out:
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="sre_agent.decisions")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export", help="etiketli raporları JSON olarak stdout'a yaz (DB)")
    e = sub.add_parser("evaluate", help="etiketli raporlarda karar ölçümü")
    e.add_argument("--models", type=Path, default=Path("/etc/sre-agent/models.yaml"))
    e.add_argument("--limit", type=int, default=100_000)
    e.add_argument("--save", action="store_true", help="kararları reports.decisions'a yaz")
    e.add_argument("--from-file", type=Path, help="DB yerine export dosyası")
    e.add_argument("--per-report", action="store_true", help="olay yerine rapor başına say")
    p = sub.add_parser("profile", help="T2.4: düşünme açık/kapalı karşılaştırması")
    p.add_argument("--models", type=Path, default=Path("/etc/sre-agent/models.yaml"))
    p.add_argument("--from-file", type=Path, required=True)
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.cmd == "export":
        return asyncio.run(export())
    if args.cmd == "evaluate":
        return asyncio.run(
            evaluate(args.models, args.limit, args.save, args.from_file, not args.per_report)
        )
    return asyncio.run(profile(args.models, args.from_file, args.n, args.out))


if __name__ == "__main__":
    sys.exit(main())
