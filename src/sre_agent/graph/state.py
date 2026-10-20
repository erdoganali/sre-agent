"""Graph state: her graph node ortak state'i okur, yalnız kendi alanına yazar.

Paralel specialist'ler (Send fan-out) aynı adımda yazar; LangGraph bir anahtara tek yazar ister.
Bu yüzden findings sözlük birleştirme (her specialist kendi anahtarı), hatalar liste ekleme
reducer'ıyla birleşir. Değerler JSON'a çevrilebilir tutulur (Postgres checkpoint).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_findings(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Specialist bulgularını birleştirir; deep dive cevabı önceki bulgunun gözlemlerine eklenir."""
    out = dict(old or {})
    for name, f in (new or {}).items():
        prev = out.get(name)
        if prev and f.get("deep_dive"):
            merged = dict(prev)
            merged["observations"] = [*prev.get("observations", []), *f.get("observations", [])]
            merged["evidence"] = [*prev.get("evidence", []), *f.get("evidence", [])]
            merged["suspects"] = sorted({*prev.get("suspects", []), *f.get("suspects", [])})
            merged["confidence"] = f.get("confidence", prev.get("confidence"))
            out[name] = merged
        else:
            out[name] = f
    return out


class GraphState(TypedDict, total=False):
    incident_id: str
    reports: list[dict[str, Any]]  # incident'ın raporları (Report JSON)
    brief: str  # specialist'lere ortak başlangıç (prefix caching için hep aynı başlar)
    knowledge: list[dict[str, Any]]  # bilgi tabanından brief'e eklenen bilinen arızalar (RAG)
    evidence: dict[str, list[dict[str, Any]]]  # yetenek → Faz 1 anomali kanıtı
    rule_category: str | None  # Faz 1 kural kararı (yedek yol için)
    specialists: list[str]
    findings: Annotated[dict[str, Any], merge_findings]
    correlation: dict[str, Any] | None
    evaluation: dict[str, Any] | None
    deep_dive_round: int
    deep_dive_tasks: list[dict[str, str]]
    budget: dict[str, Any]  # bütçe özeti (wrap_up / exhausted)
    errors: Annotated[list[str], operator.add]
    report: dict[str, Any] | None
