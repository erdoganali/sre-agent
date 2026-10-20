"""Beklenen sonuç ile gerçekleşenin karşılaştırılması (yan etkisiz; birim testli)."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Expected(BaseModel):
    """scenarios/<ad>/expected.yaml içindeki `expected` bölümü."""

    model_config = ConfigDict(extra="forbid")

    alerts: list[str]
    alert_within_min: int
    category: str
    root_cause: str
    evidence: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    # Doluysa senaryo koşulmaz (gerekçe); ör. enjeksiyon yöntemi arızayı oluşturamıyor
    deferred: str | None = None


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    namespace: str
    tool: str
    verify: str
    expected: Expected
    notes: str = ""


def load_scenario(path: Path) -> Scenario:
    return Scenario.model_validate(yaml.safe_load((path / "expected.yaml").read_text()))


class Verdict(StrEnum):
    PASS = "geçti"
    PARTIAL = "kısmen"
    FAIL = "kaldı"
    SKIPPED = "atlandı"


class Outcome(BaseModel):
    verdict: Verdict
    reasons: list[str]
    alerts_missing: list[str]
    evidence_found: list[str]
    evidence_missing: list[str]
    category_found: bool
    noise: list[str]  # beklenmeyen anomaliler (hata sayılmaz; önceki değişiklikler, yan etkiler)


def evaluate(exp: Expected, fired: set[str], report: dict[str, Any] | None) -> Outcome:
    """Alarm çaldı mı, rapor yazıldı mı, beklenen kanıt ve kategori raporda var mı."""
    alerts_missing = [a for a in exp.alerts if a not in fired]
    if report is None:
        why = ["rapor yazılmadı"]
        if alerts_missing:
            why.insert(0, f"alarm çalmadı: {', '.join(alerts_missing)}")
        return Outcome(
            verdict=Verdict.FAIL,
            reasons=why,
            alerts_missing=alerts_missing,
            evidence_found=[],
            evidence_missing=list(exp.evidence),
            category_found=False,
            noise=[],
        )
    anomalies = {e["check"] for e in report.get("evidence", []) if e.get("status") == "anomaly"}
    found = [c for c in exp.evidence if c in anomalies]
    missing = [c for c in exp.evidence if c not in anomalies]
    category_found = exp.category in (report.get("anomalies_by_category") or {})
    reasons: list[str] = []
    if alerts_missing:
        reasons.append(f"alarm çalmadı: {', '.join(alerts_missing)}")
    if missing:
        reasons.append(f"beklenen kanıt yok: {', '.join(missing)}")
    if not category_found:
        reasons.append(f"beklenen kategori ({exp.category}) raporda yok")
    if not reasons:
        verdict = Verdict.PASS
    elif alerts_missing:
        verdict = Verdict.FAIL
    else:
        verdict = Verdict.PARTIAL
    return Outcome(
        verdict=verdict,
        reasons=reasons or ["alarm, kanıt ve kategori beklendiği gibi"],
        alerts_missing=alerts_missing,
        evidence_found=found,
        evidence_missing=missing,
        category_found=category_found,
        noise=sorted(anomalies - set(exp.evidence)),
    )
