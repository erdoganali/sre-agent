"""Servis yapılandırması (config/<ortam>.yaml). Ortama özel her şey burada ve adaptörlerde."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sre_agent.decisions import DecisionConfig
from sre_agent.freshness import FreshnessProbe
from sre_agent.graph.investigator import InvestigationConfig
from sre_agent.knowledge import KnowledgeConfig
from sre_agent.packs import Config as PacksConfig


class TlsEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    port: int = 443
    sni: str | None = None


class AppConfig(PacksConfig):
    model_config = ConfigDict(extra="forbid")

    prometheus_url: str | None = None
    loki_url: str | None = None
    tempo_url: str | None = None
    grafana_url: str | None = None
    # Kubernetes: cluster içinden (ServiceAccount) ya da kubeconfig'teki bu context'ten
    kube_context: str | None = None
    argocd_namespace: str | None = None  # boşsa Argo CD adaptörü kullanılmaz (GKE)
    tls_endpoints: list[TlsEndpoint] = Field(default_factory=list)

    # Rapor penceresi alarmın başlangıcından bu kadar önce açılır
    window_before_min: int = 30
    # Aynı alarm (fingerprint) bu süre içinde tekrar gelirse yeniden koşulmaz
    dedupe_min: int = 10
    # Plan: aynı anda en fazla 2 koşu (başlangıç)
    max_concurrent_runs: int = 2
    keep_reports: int = 200  # bellekte (Postgres yoksa ya da yazılamazken)
    # Etiketsiz raporların saklama süresi; etiketli raporlar silinmez (eğitim verisi)
    retention_days: int = 180
    # Pakette eşlemesi olmayan alarmlar için çalışacak kontroller (boşsa tüm çekirdek kontroller)
    default_checks: list[str] = Field(default_factory=list)
    # Kontrol adı → sorgu anahtarı → sorgu (ortama özel metrikler)
    query_overrides: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Veri tazeliği sorguları (ortama özgü): rapor kaynakların yaşını yazar, bayatsa kör nokta
    freshness: list[FreshnessProbe] = Field(default_factory=list)
    # Grafana anotasyonunda rapora bağlantı için (ör. ingress adresi); boşsa bağlantı yok
    report_base_url: str | None = None
    # Karar noktaları (T2.5, ADR-028): off | shadow | model; model kataloğu models_file'da
    decisions: DecisionConfig = Field(default_factory=DecisionConfig)
    # Investigation graph (T3.2): off | shadow | on; incident sessizleşince bir kez koşar
    investigation: InvestigationConfig = Field(default_factory=InvestigationConfig)
    # Bilgi tabanı (RAG, BM25): graph brief'ine bilinen arızalar, product'ta arama aracı
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)


def load_app_config(path: Path) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(path.read_text()) or {})
