"""Investigation graph'ın tipli çıktıları (T3.2).

Graph node'ları arasında serbest metin değil bunlar dolaşır: correlate her specialist'in çıktısını
farklı yorumlamasın diye."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EvidenceRef(_M):
    """Bulgunun dayandığı kanıt: hangi kontrol ya da araç, ne döndü (kısa)."""

    source: str  # kontrol adı ya da araç çağrısı
    summary: str


class Finding(_M):
    specialist: str
    summary: str
    observations: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    # Bu specialist'in işaret ettiği kök neden adayları (taksonomi kategorileri)
    suspects: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"
    error: str | None = None  # specialist çalışamadı (finding düşük güvenli sayılır)
    tools_used: list[str] = Field(default_factory=list)  # çağrılan araçlar, sırayla (T3.4)


class Rejected(_M):
    """Değerlendirilip reddedilen hipotez ve nedeni (açıklanabilirlik; ADR-029)."""

    category: str
    reason: str


class Correlation(_M):
    category: str  # taksonomi kategorisi ya da "unknown"
    root_cause: str
    supporting: list[str] = Field(default_factory=list)  # bu kök nedeni gösteren specialist'ler
    rejected: list[Rejected] = Field(default_factory=list)  # neyi neden reddetti
    contributing: list[str] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    method: Literal["model", "passthrough", "fallback"] = "model"


class DeepDive(_M):
    specialist: str
    question: str


class Evaluation(_M):
    confidence: Confidence
    cross_validated: bool  # kök neden en az iki bağımsız specialist'te görüldü mü
    suggested_deep_dives: list[DeepDive] = Field(default_factory=list)
    note: str = ""


class FinalReport(_M):
    summary: str  # en çok SUMMARY_MAX karakter; ayrıntı `details`'te (kodda kırpılır)
    category: str
    root_cause: str
    details: str = ""
    evidence_chain: list[str] = Field(default_factory=list)
    rejected: list[Rejected] = Field(default_factory=list)  # reddedilen hipotezler ve nedeni
    recommendations: list[str] = Field(default_factory=list)
    # Kodda hesaplanır (modelin değeri yok sayılır): güven yüksek, çapraz doğrulanmış, bütçe
    # dolmamış ve rapor modelden geldiyse True
    certain: bool = False
    source: Literal["model", "fallback"] = "model"
    deep_dive_count: int = 0
