"""LLM'siz kanıt raporu: kontroller taksonomi gruplarına göre sıralı, her bulgu sorgusuyla."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from sre_agent.checks import Evidence, Status, Window
from sre_agent.freshness import SourceState, SourceStatus
from sre_agent.taxonomy import load as load_taxonomy

_STATUS_ORDER = {Status.ANOMALY: 0, Status.ERROR: 1, Status.NO_DATA: 2, Status.OK: 3}
_ICON = {Status.ANOMALY: "🔴", Status.ERROR: "⚠️", Status.NO_DATA: "⚪", Status.OK: "🟢"}


class AlertRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    fingerprint: str
    labels: dict[str, str]
    starts_at: datetime


class Report(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    alert: AlertRef
    window: Window
    generated_at: datetime
    duration_ms: float
    evidence: tuple[Evidence, ...]
    anomalies_by_category: dict[str, int]
    sources: tuple[SourceStatus, ...] = ()
    blind_spots: tuple[str, ...] = ()  # boş değilse rapor eksik veriyle üretildi
    packs: tuple[str, ...] = ()  # kontrolleri eşlemesiyle seçen domain paketleri
    annotation_id: int | None = None
    incident_id: str | None = None  # olay gruplama (T3.1)
    incident_reason: str | None = None  # bu olaya neden katıldı ("yeni olay" dahil)

    @property
    def anomalies(self) -> list[Evidence]:
        return [e for e in self.evidence if e.status is Status.ANOMALY]


def primary_group(e: Evidence) -> str | None:
    """Kontrolün raporda göründüğü grup: kategorilerinin taksonomide en önde gelen grubu.

    Sıralama ve başlık aynı fonksiyonu kullanır; ayrı hesaplanınca birden çok kategorili
    kontrollerde başlıklar tekrar ediyordu.
    """
    t = load_taxonomy()
    order = {g.id: i for i, g in enumerate(t.groups)}
    group_of = {c.id: c.group for c in t.categories}
    groups = [group_of[c] for c in e.categories if c in group_of]
    return min(groups, key=order.__getitem__) if groups else None


def order_evidence(evidence: list[Evidence]) -> tuple[Evidence, ...]:
    """Taksonomi grubu (Değişiklik → … → Güvenlik), sonra durum (anomali önce), sonra ad."""
    order = {g.id: i for i, g in enumerate(load_taxonomy().groups)}

    def key(e: Evidence) -> tuple[int, int, str]:
        group = primary_group(e)
        return (order[group] if group else 99, _STATUS_ORDER[e.status], e.check)

    return tuple(sorted(evidence, key=key))


def build_report(
    report_id: str,
    alert: AlertRef,
    window: Window,
    generated_at: datetime,
    duration_ms: float,
    evidence: list[Evidence],
    sources: list[SourceStatus] | None = None,
    blind_spots: list[str] | None = None,
    packs: list[str] | None = None,
) -> Report:
    tally: Counter[str] = Counter(
        c for e in evidence if e.status is Status.ANOMALY for c in e.categories
    )
    return Report(
        id=report_id,
        alert=alert,
        window=window,
        generated_at=generated_at,
        duration_ms=duration_ms,
        evidence=order_evidence(evidence),
        anomalies_by_category=dict(tally.most_common()),
        sources=tuple(sources or ()),
        blind_spots=tuple(blind_spots or ()),
        packs=tuple(packs or ()),
    )


def to_markdown(report: Report) -> str:
    t = load_taxonomy()
    names = {c.id: c.name for c in t.categories}
    groups = {g.id: g.name for g in t.groups}
    a = report.alert
    lines = [f"# {a.name}", ""]
    if report.incident_id:
        lines += [
            f"> **Olay:** {report.incident_id} ({report.incident_reason or ''}) — "
            f"tüm raporlar ve olay etiketi: /incidents/{report.incident_id}",
            "",
        ]
    if report.blind_spots:
        lines += [
            "> ⚠ **Kör nokta:** bu rapor eksik ya da bayat veriyle üretildi; "
            '"anomali yok" sonucu güvenilir değil.',
            *[f"> - {b}" for b in report.blind_spots],
            "",
        ]
    lines += [
        f"- **Etiketler:** {', '.join(f'{k}={v}' for k, v in sorted(a.labels.items()))}",
        f"- **Pencere:** {report.window.start:%Y-%m-%d %H:%M} - {report.window.end:%H:%M} UTC",
        *([f"- **Paket:** {', '.join(report.packs)}"] if report.packs else []),
        f"- **Süre:** {report.duration_ms / 1000:.1f} sn, {len(report.evidence)} kontrol, "
        f"{len(report.anomalies)} anomali",
    ]
    if report.anomalies_by_category:
        tally = ", ".join(
            f"{names.get(c, c)} ({n})" for c, n in report.anomalies_by_category.items()
        )
        lines.append(f"- **Anomali olan kategoriler:** {tally}")
    if report.sources:
        icon = {SourceState.FRESH: "🟢", SourceState.STALE: "🟠", SourceState.UNREACHABLE: "🔴"}
        lines += ["", "## Veri kaynakları", ""]
        for src in report.sources:
            age = f", son veri {src.age_s:.0f} sn önce" if src.age_s is not None else ""
            detail = f" ({src.detail})" if src.detail else ""
            lines.append(f"- {icon.get(src.state, '⚪')} {src.name}: {src.state}{age}{detail}")
    current = None
    for e in report.evidence:
        group = groups.get(primary_group(e) or "", "Diğer")
        if group != current:
            lines += ["", f"## {group}", ""]
            current = group
        lines.append(f"- {_ICON[e.status]} **{e.check}** ({e.status}): {e.summary}")
        if e.status in (Status.ANOMALY, Status.ERROR) and e.query:
            lines.append("  ```")
            lines += [f"  {q}" for q in e.query.splitlines()]
            lines.append("  ```")
        if e.error:
            lines.append(f"  hata: {e.error}")
    return "\n".join(lines) + "\n"


def annotation_text(report: Report, base_url: str | None) -> str:
    """Grafana anotasyonu: tek satırlık özet (liste paneli satır sonlarını yok sayar)."""
    n = len(report.anomalies)
    if n:
        items = "; ".join(f"{e.check}: {e.summary}" for e in report.anomalies[:5])
        more = f" (+{n - 5})" if n > 5 else ""
        text = f"SRE raporu — {report.alert.name}: {n} anomali — {items}{more}"
    else:
        text = f"SRE raporu — {report.alert.name}: anomali yok ({len(report.evidence)} kontrol)"
    if report.blind_spots:
        text = f"⚠ kör nokta ({'; '.join(report.blind_spots)}) — {text}"
    if base_url:
        text += f" — {base_url.rstrip('/')}/reports/{report.id}.md"
    return text


def summary_log(report: Report) -> dict[str, Any]:
    """Tek satır JSON log kaydı."""
    return {
        "event": "report",
        "id": report.id,
        "alert": report.alert.name,
        "labels": report.alert.labels,
        "anomalies": [e.check for e in report.anomalies],
        "by_category": report.anomalies_by_category,
        "duration_ms": round(report.duration_ms),
        "annotation_id": report.annotation_id,
        "blind_spots": list(report.blind_spots),
        "packs": list(report.packs),
    }
