"""Investigation sonucunun rapordaki bölümü (T3.5).

`on` kipinde bölüm raporun başına gelir ve etkili sonuçtur; `shadow` kipinde sona eklenir ve
yalnız ölçüm içindir. Girdi `Investigator.run`'ın kaydettiği sözlüktür (incidents.investigation).
"""

from __future__ import annotations

from typing import Any

from sre_agent.taxonomy import load as load_taxonomy

CONFIDENCE = {"high": "yüksek", "medium": "orta", "low": "düşük"}


def attach(md: str, inv: dict[str, Any] | None, mode: str | None) -> str:
    """Bölümü rapor markdown'ına ekler: `on`'da kontrol sonuçlarından önce, değilse sona."""
    section = markdown(inv, mode)
    if not section:
        return md
    if mode == "on" and (i := md.find("\n## ")) >= 0:
        return md[: i + 1] + section + md[i + 1 :]
    return md + "\n" + section


def markdown(inv: dict[str, Any] | None, mode: str | None) -> str:
    if not inv or not inv.get("report"):
        return ""
    r = inv["report"]
    names = {c.id: c.name for c in load_taxonomy().categories}
    cat = r.get("category") or "unknown"
    ev = inv.get("evaluation") or {}
    shadow = mode != "on"
    head = "## Soruşturma (agent" + (", gölge: yalnız ölçüm" if shadow else "") + ")"
    lines = [head, ""]
    if r.get("source") == "fallback":
        lines += ["> Model erişilemedi; sonuç kurallarla üretildi.", ""]
    lines += [
        f"**{r.get('summary', '').strip()}**",
        "",
        f"- **Kategori:** {names.get(cat, cat)} (`{cat}`)",
        f"- **Kök neden:** {r.get('root_cause', '').strip()}",
        f"- **Güven:** {CONFIDENCE.get(ev.get('confidence', ''), '-')}"
        + ("; iki bağımsız uzmanda doğrulandı" if ev.get("cross_validated") else "")
        + ("" if r.get("certain") else "; kesin değil, insan onayı gerekir"),
    ]
    if r.get("details"):
        lines += ["", r["details"].strip()]
    if r.get("evidence_chain"):
        lines += ["", "### Kanıt zinciri", "", *[f"1. {x}" for x in r["evidence_chain"]]]
    if r.get("rejected"):
        lines += ["", "### Reddedilen hipotezler", ""]
        lines += [f"- `{x.get('category')}`: {x.get('reason')}" for x in r["rejected"]]
    if r.get("recommendations"):
        lines += ["", "### Öneriler (salt okunur ajan; uygulamaz)", ""]
        lines += [f"- {x}" for x in r["recommendations"]]
    known = inv.get("knowledge") or []
    if known:
        lines += ["", "### Bilgi tabanından benzer arızalar (RAG)", ""]
        lines += [f"- {k.get('title')} (`{k.get('category')}`)" for k in known]
    findings = list((inv.get("findings") or {}).values())  # specialist adı → Finding
    if findings:
        lines += [
            "",
            "### Uzmanlar",
            "",
            "| uzman | güven | araçlar | özet |",
            "| --- | --- | --- | --- |",
        ]
        for f in findings:
            tools = ", ".join(f.get("tools_used") or []) or "-"
            summary = (
                (f.get("error") or f.get("summary") or "").replace("|", "/").replace("\n", " ")
            )
            lines.append(
                f"| {f.get('specialist')} | {CONFIDENCE.get(f.get('confidence', ''), '-')} "
                f"| {tools} | {summary[:300]} |"
            )
    used = (inv.get("budget") or {}).get("used")
    if used is not None:
        lines += ["", f"Token: {used:,}".replace(",", ".")]
    return "\n".join(lines) + "\n\n"
