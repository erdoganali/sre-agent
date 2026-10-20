"""Specialist ölçümü (T3.4 kabulü): etiketli olaylarda her specialist doğru kanıtı buldu mu?

Graph olay başına koşar (kayıt yapılmaz); her specialist'in kanıt satırları etikete göre
`eval/specialist-expectations.yaml`'daki desenlerle karşılaştırılır. Sonuç olay ve specialist
tablosu ve specialist başına özet.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def load_expectations(path: Path) -> dict[str, list[re.Pattern[str]]]:
    raw = yaml.safe_load(path.read_text()) or {}
    return {cat: [re.compile(p, re.IGNORECASE) for p in pats] for cat, pats in raw.items()}


def found(finding: dict[str, Any], patterns: list[re.Pattern[str]]) -> bool:
    return any(
        p.search(f"{e.get('source', '')} {e.get('summary', '')}")
        for e in finding.get("evidence") or []
        for p in patterns
    )


def label_of(records: list[dict[str, Any]]) -> str:
    """Olayın etiketi: üye raporlarda en sık etiket."""
    return Counter(r.get("label_category") or "unknown" for r in records).most_common(1)[0][0]


@dataclass
class Row:
    incident: str
    label: str
    specialist: str
    calls: int
    tools: list[str]
    ok: bool | None  # None: model yoktu ya da etikete desen yok
    suspects: list[str]
    error: str | None


def rows_for(
    incident: str, label: str, result: dict[str, Any], exp: dict[str, list[re.Pattern[str]]]
) -> list[Row]:
    out = []
    for name, f in sorted((result.get("findings") or {}).items()):
        tools = list(f.get("tools_used") or [])
        modelled = bool(tools) and not f.get("error")
        pats = exp.get(label)
        out.append(
            Row(
                incident=incident,
                label=label,
                specialist=name,
                calls=len(tools),
                tools=tools,
                ok=found(f, pats) if modelled and pats else None,
                suspects=list(f.get("suspects") or []),
                error=f.get("error"),
            )
        )
    return out


def render(rows: list[Row], reports: dict[str, dict[str, Any]]) -> str:
    lines = [
        "| olay | etiket | specialist | çağrı | araçlar | doğru kanıt | adaylar | rapor |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        rep = reports.get(r.incident) or {}
        mark = "-" if r.ok is None else ("✓" if r.ok else "✗")
        if r.error:
            mark = "yedek"
        tools = ", ".join(sorted(set(t.split(".", 1)[-1] for t in r.tools))) or "-"
        lines.append(
            f"| {r.incident} | {r.label} | {r.specialist} | {r.calls} | {tools} | {mark} | "
            f"{', '.join(r.suspects) or '-'} | {rep.get('category', '-')} |"
        )
    per: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        if r.ok is not None:
            per[r.specialist].append(r.ok)
    lines += ["", "| specialist | doğru kanıt | ölçülen olay |", "|---|---|---|"]
    for name, oks in sorted(per.items()):
        lines.append(f"| {name} | {sum(oks)} | {len(oks)} |")
    correct = sum(
        1
        for i, rep in reports.items()
        if rep.get("category") == next((r.label for r in rows if r.incident == i), None)
    )
    lines.append(f"\nRapor kategorisi etiketle aynı: {correct}/{len(reports)} olay")
    return "\n".join(lines)
