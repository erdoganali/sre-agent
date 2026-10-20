"""Karar noktaları (T2.5, ADR-028).

Model olasılık verir; "bilinmiyor", "değerlendirilemedi" ve yönlendirme kodda kararlaştırılır.
Her karar noktasının kural tabanlı karşılığı vardır; model kapalıyken ya da gölge modda etkili
karar kurallardan gelir.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

UNKNOWN = "unknown"  # taksonomideki genel sonuç; model seçeneği değil
NOT_ASSESSABLE = "not_assessable"  # kapsama düşük: karar verilmedi


class Mode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"  # kurallar karar verir, model yalnız kaydedilir
    MODEL = "model"  # model karar verir; kullanılamazsa kurallar


class Point(StrEnum):
    KNOWN = "known"  # bilinen olay mı (benzerlik)
    NOISE = "noise"  # gürültü mü (model / kurallar)
    GROUP = "group"
    CATEGORY = "category"


class Thresholds(BaseModel):
    """Başlangıç değerleri geçicidir; T2.6 kalibrasyonuyla değişir (ADR-028)."""

    model_config = ConfigDict(extra="forbid")

    min_top: float = 0.6  # en yüksek olasılık bunun altındaysa "bilinmiyor"
    min_margin: float = 0.15  # ilk iki seçenek farkı bunun altındaysa "bilinmiyor"
    min_coverage: float = 0.5  # veri alabilen kontrol oranı bunun altındaysa "değerlendirilemedi"
    noise_flag: float = 0.8  # gürültü olasılığı bunun üstündeyse işaretlenir (bastırılmaz)
    # Anomali kümesi Jaccard benzerliği. Ölçüm (2026-10-11, bir pod-restart alarmının 204 raporu,
    # etiketli node kaybı raporuna karşı): 0,8 → node kaybının %8'i tanındı; 0,6 → %44, yanlış
    # "bilinen" 0; 0,5 → yanlış "bilinen" %45. Ayırt edicilik (IDF) ağırlığı daha kötü (%7-21).
    known_similarity: float = 0.6


class DecisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = Mode.OFF
    models_file: str = "/etc/sre-agent/models.yaml"
    thresholds: Thresholds = Field(default_factory=Thresholds)
    # Model girdisinde anomali başına en çok bu kadar veri satırı (güvenilmez bölüm)
    max_example_lines: int = 3
    max_example_chars: int = 200


class Decision(BaseModel):
    point: Point
    source: Literal["model", "rules", "similarity"]
    effective: bool  # raporda kullanılan karar mı (gölgedeki model kararı False)
    outcome: str  # seçenek, UNKNOWN ya da NOT_ASSESSABLE
    probability: float | None = None  # sonucun olasılığı (kategori için grup x grup içi)
    probs: dict[str, float] = Field(default_factory=dict)
    margin: float | None = None
    reason: str = ""
    model: str | None = None
    method: str | None = None  # logprobs | counts | jaccard
    ref_report: str | None = None  # bilinen olayda benzeyen rapor
    error: str | None = None


class DecisionSet(BaseModel):
    mode: Mode
    taxonomy_version: str
    input_hash: str
    thresholds: Thresholds
    decisions: list[Decision]
    noise_flag: bool = False  # öncelik düşürme işareti; rapor bastırılmaz

    def effective(self, point: Point) -> Decision | None:
        return next((d for d in self.decisions if d.point is point and d.effective), None)


def input_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def decide(probs: dict[str, float], th: Thresholds) -> tuple[str, float, float]:
    """Dağılımdan sonuç: (seçenek ya da UNKNOWN, en yüksek olasılık, ilk iki arası fark)."""
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return UNKNOWN, 0.0, 0.0
    top_label, top = ranked[0]
    margin = top - (ranked[1][1] if len(ranked) > 1 else 0.0)
    if top < th.min_top or margin < th.min_margin:
        return UNKNOWN, top, margin
    return top_label, top, margin
