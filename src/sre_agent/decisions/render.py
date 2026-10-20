"""Kararların insan için gösterimi (rapor .md ve etiket sayfası)."""

from __future__ import annotations

import html
from typing import Any

from sre_agent.taxonomy import load as load_taxonomy

POINT_NAMES = {"known": "Bilinen olay", "noise": "Gürültü", "group": "Grup", "category": "Kategori"}
SOURCE_NAMES = {"rules": "kurallar", "model": "model", "similarity": "benzerlik"}


def _names() -> dict[str, str]:
    t = load_taxonomy()
    out = {c.id: c.name for c in t.categories} | {g.id: g.name for g in t.groups}
    return out | {"unknown": "bilinmiyor", "not_assessable": "değerlendirilemedi"}


def rows(decisions: list[dict[str, Any]]) -> list[dict[str, str]]:
    names = _names()
    out = []
    for d in decisions:
        p = d.get("probability")
        out.append(
            {
                "nokta": POINT_NAMES.get(d["point"], d["point"]),
                "kaynak": SOURCE_NAMES.get(d["source"], d["source"])
                + ("" if d.get("effective") else " (gölge)"),
                "sonuç": "hata" if d.get("error") else names.get(d["outcome"], d["outcome"]),
                "olasılık": f"{p:.2f}" if isinstance(p, int | float) else "",
                "not": (d.get("error") or d.get("reason") or "")[:160],
            }
        )
    return out


def markdown(decisions: list[dict[str, Any]]) -> str:
    if not decisions:
        return ""
    mode = decisions[0].get("mode", "")
    lines = [
        "",
        f"## Kararlar (mod: {mode})",
        "",
        "Etkili karar raporda kullanılandır; gölge kararlar yalnız ölçüm için kaydedilir.",
        "",
        "| Nokta | Kaynak | Sonuç | Olasılık | Not |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows(decisions):
        cells = [r[k].replace("|", "/") for k in ("nokta", "kaynak", "sonuç", "olasılık", "not")]
        lines.append("| " + " | ".join(cells) + " |")
    if any(d.get("noise_flag") for d in decisions):
        lines += ["", "> Gürültü olarak işaretlendi (rapor bastırılmadı; öncelik düşük)."]
    return "\n".join(lines) + "\n"


def _match(d: dict[str, Any], label: str | None, label_group: str | None) -> str:
    """Kategori satırı etiketin kendisiyle, grup satırı etiketin grubuyla karşılaştırılır."""
    if not label or d.get("error"):
        return ""
    if d["outcome"] in ("unknown", "not_assessable"):
        return "?"  # karar vermedi: yanlış sayılmaz
    if d["point"] == "category":
        return "✓" if d["outcome"] == label else "✗"
    if d["point"] == "group" and label_group:
        return "✓" if d["outcome"] == label_group else "✗"
    return ""


def html_table(decisions: list[dict[str, Any]], label: str | None) -> str:
    """Etiket kaydedildikten sonra gösterilir (önce gösterilirse etiketi yönlendirir)."""
    if not decisions:
        return "<p><i>Bu rapor için karar kaydı yok.</i></p>"
    e = html.escape
    t = load_taxonomy()
    label_group = next((c.group for c in t.categories if c.id == label), None)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{e(r[k])}</td>" for k in ("nokta", "kaynak", "sonuç", "olasılık", "not"))
        + f"<td>{_match(d, label, label_group)}</td>"
        + "</tr>"
        for r, d in zip(rows(decisions), decisions, strict=True)
    )
    return (
        "<h3>Sistemin kararları (etiketinle karşılaştır)</h3>"
        '<table class="dec"><tr><th>Nokta</th><th>Kaynak</th><th>Sonuç</th>'
        "<th>Olasılık</th><th>Not</th><th>Etiketle aynı (✓ / ✗ / ? karar yok)</th></tr>"
        f"{body}</table>"
    )
