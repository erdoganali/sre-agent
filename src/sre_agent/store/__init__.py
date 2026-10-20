"""Rapor deposu portu. Uygulama veritabanını yalnızca DATABASE_URL ile tanır (ADR-023)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from sre_agent.decisions import DecisionSet
from sre_agent.incidents import Incident
from sre_agent.report import Report


class Versions(BaseModel):
    """Rapor anındaki şema/kod sürümleri: eski etiketlerin hangi şemaya göre verildiği bilinsin."""

    model_config = ConfigDict(frozen=True)

    app: str
    taxonomy: str
    packs: dict[str, str] = Field(default_factory=dict)


class Label(BaseModel):
    """İnsanın verdiği doğru cevap: kategori + kök neden (karar modelinin eğitim verisi)."""

    model_config = ConfigDict(frozen=True)

    category: str
    root_cause: str = Field(min_length=3, max_length=1000)
    labeled_by: str = Field(min_length=1, max_length=100)


@runtime_checkable
class ReportStore(Protocol):
    async def save(self, report: Report, versions: Versions) -> None: ...

    async def resolve(self, fingerprint: str, at: datetime) -> int:
        """Bu fingerprint'in çözülmemiş raporlarına çözülme zamanı yazar; güncellenen sayı."""
        ...

    async def get(self, report_id: str) -> dict[str, Any] | None: ...

    async def recent(self, limit: int, include_test: bool) -> list[dict[str, Any]]: ...

    async def set_label(self, report_id: str, label: Label, at: datetime) -> bool: ...

    async def save_decisions(self, report_id: str, dset: DecisionSet, at: datetime) -> None:
        """Raporun karar kayıtlarını yazar (varsa öncekilerin yerine; ADR-028)."""
        ...

    async def save_incident(self, inc: Incident) -> None: ...

    async def set_report_incident(self, report_id: str, incident_id: str, why: str) -> None: ...

    async def recent_reports(self, since: datetime) -> list[dict[str, Any]]:
        """Verilen andan sonraki raporlar: id, report (tam), incident_id."""
        ...

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        """Olay ve üye raporlarının özeti (reports anahtarında)."""
        ...

    async def label_incident(self, incident_id: str, label: Label, at: datetime) -> int:
        """Olayı ve bütün üye raporlarını etiketler; güncellenen rapor sayısı."""
        ...

    async def incident_reports(self, incident_id: str) -> list[dict[str, Any]]:
        """Incident'ın raporlarının tam JSON'u, zaman sırasıyla."""
        ...

    async def save_investigation(
        self, incident_id: str, result: dict[str, Any], mode: str, at: datetime
    ) -> None:
        """Investigation graph'ın sonucunu incident'a yazar."""
        ...

    async def get_decisions(self, report_id: str) -> list[dict[str, Any]]:
        """Raporun karar kayıtları (reports.decisions satırları)."""
        ...

    async def labeled_history(self, alertname: str, limit: int = 50) -> list[dict[str, Any]]:
        """Aynı alarmın etiketli raporları: id, alert, anomaly_checks, label_category."""
        ...

    async def purge(self, older_than: datetime) -> int:
        """Etiketsiz ve belirtilen tarihten eski raporları siler (etiketliler korunur)."""
        ...
