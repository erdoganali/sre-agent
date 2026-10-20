"""Karar noktalarının kural tabanlı karşılıkları (model yokken ve gölge modda etkili karar)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sre_agent.checks import Status
from sre_agent.decisions import (
    NOT_ASSESSABLE,
    UNKNOWN,
    Decision,
    Point,
    Thresholds,
    decide,
)
from sre_agent.report import Report
from sre_agent.taxonomy import Taxonomy

NOISE_LABELS = ("olay", "gürültü")


def coverage(report: Report) -> float:
    """Veri alabilen (ok ya da anomaly) kontrollerin oranı."""
    if not report.evidence:
        return 0.0
    usable = sum(e.status in (Status.OK, Status.ANOMALY) for e in report.evidence)
    return usable / len(report.evidence)


def anomaly_checks(report: Report) -> frozenset[str]:
    return frozenset(e.check for e in report.evidence if e.status is Status.ANOMALY)


def known(report: Report, history: Iterable[dict[str, Any]], th: Thresholds) -> Decision:
    """Bilinen olay: aynı alarm adı ve benzer anomali kümesi, etiketli geçmiş rapor. Modelsiz."""
    mine = anomaly_checks(report)
    best: tuple[float, dict[str, Any]] | None = None
    for h in history:
        if h.get("id") == report.id or h.get("alert") != report.alert.name:
            continue
        if not h.get("label_category"):
            continue
        theirs = frozenset(h.get("anomaly_checks") or ())
        union = mine | theirs
        sim = len(mine & theirs) / len(union) if union else 1.0
        if best is None or sim > best[0]:
            best = (sim, h)
    if best is None or best[0] < th.known_similarity:
        sim = best[0] if best else 0.0
        return Decision(
            point=Point.KNOWN,
            source="similarity",
            effective=True,
            outcome="yeni",
            probability=None,
            reason=f"etiketli benzer rapor yok (en yüksek benzerlik {sim:.2f})",
            method="jaccard",
        )
    sim, h = best
    return Decision(
        point=Point.KNOWN,
        source="similarity",
        effective=True,
        outcome="bilinen",
        probability=sim,
        reason=f"{h['id']} ile aynı alarm, anomali kümesi benzerliği {sim:.2f}; "
        f"etiketi {h['label_category']}",
        method="jaccard",
        ref_report=str(h["id"]),
    )


def noise(report: Report, th: Thresholds, effective: bool) -> Decision:
    """Kaba kural: kapsama yeterli ve hiçbir kontrol anomali görmediyse gürültü adayı."""
    is_noise = not anomaly_checks(report)
    probs = {"olay": 0.0 if is_noise else 1.0, "gürültü": 1.0 if is_noise else 0.0}
    return Decision(
        point=Point.NOISE,
        source="rules",
        effective=effective,
        outcome="gürültü" if is_noise else "olay",
        probability=1.0,
        probs=probs,
        reason="hiçbir kontrol anomali görmedi" if is_noise else "anomali kanıtı var",
        method="counts",
    )


def classify(report: Report, taxonomy: Taxonomy, th: Thresholds, effective: bool) -> list[Decision]:
    """Grup → kategori, kontrollerin anomali sayımından (anomalies_by_category)."""
    counts = {
        c: n
        for c, n in report.anomalies_by_category.items()
        if any(x.id == c for x in taxonomy.categories)
    }
    total = sum(counts.values())
    if not total:
        return [
            Decision(
                point=Point.GROUP,
                source="rules",
                effective=effective,
                outcome=UNKNOWN,
                reason="kategori destekleyen anomali yok",
                method="counts",
            )
        ]
    group_of = {c.id: c.group for c in taxonomy.categories}
    groups: dict[str, float] = {g.id: 0.0 for g in taxonomy.groups}
    for c, n in counts.items():
        groups[group_of[c]] += n / total
    g_out, g_top, g_margin = decide(groups, th)
    out = [
        Decision(
            point=Point.GROUP,
            source="rules",
            effective=effective,
            outcome=g_out,
            probability=g_top,
            probs=groups,
            margin=g_margin,
            reason="anomali sayımı" if g_out != UNKNOWN else "grup belirsiz (eşik/fark)",
            method="counts",
        )
    ]
    if g_out == UNKNOWN:
        return out
    inner_total = sum(n for c, n in counts.items() if group_of[c] == g_out)
    inner = {c.id: counts.get(c.id, 0) / inner_total for c in taxonomy.in_group(g_out)}
    c_out, c_top, c_margin = decide(inner, th)
    out.append(
        Decision(
            point=Point.CATEGORY,
            source="rules",
            effective=effective,
            outcome=c_out,
            probability=round(g_top * c_top, 4),
            probs=inner,
            margin=c_margin,
            reason="grup içi anomali sayımı" if c_out != UNKNOWN else "kategori belirsiz",
            method="counts",
        )
    )
    return out


def not_assessable(cov: float, th: Thresholds, effective: bool, source: str) -> Decision:
    return Decision(
        point=Point.GROUP,
        source=source,  # type: ignore[arg-type]
        effective=effective,
        outcome=NOT_ASSESSABLE,
        reason=f"kapsama {cov:.0%} < {th.min_coverage:.0%}: kontrollerin çoğu veri alamadı",
    )
