"""Rapor servisinin kendi metrikleri (/metrics; ServiceMonitor ile Prometheus'a)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

ALERTS = Counter("sre_agent_alerts_received_total", "Webhook'la gelen alarmlar", ["status"])
DEDUPED = Counter(
    "sre_agent_alerts_deduplicated_total", "Tekrar koruması nedeniyle atlanan alarmlar"
)
REPORTS = Counter("sre_agent_reports_total", "Üretilen raporlar", ["result", "blind_spot"])
IN_PROGRESS = Gauge("sre_agent_runs_in_progress", "Süren rapor koşuları")
REPORT_SECONDS = Histogram(
    "sre_agent_report_duration_seconds",
    "Alarm alındıktan rapor yazılana kadar geçen süre",
    buckets=(1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 300),
)
CHECK_SECONDS = Histogram(
    "sre_agent_check_duration_seconds",
    "Kontrol süresi",
    ["check"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20),
)
CHECK_RESULTS = Counter("sre_agent_check_results_total", "Kontrol sonuçları", ["check", "status"])
SINK_ERRORS = Counter("sre_agent_sink_errors_total", "Rapor yazma hataları", ["sink"])
WATCHDOG_LAST_SEEN = Gauge(
    "sre_agent_watchdog_last_seen_timestamp_seconds",
    "Alertmanager'dan son Watchdog bildiriminin zamanı (hat canlılığı)",
)

# ---- Model katmanı ve token bütçesi (T2.3, ADR-004)
MODEL_REQUESTS = Counter(
    "sre_agent_model_requests_total",
    "Model çağrıları",
    ["model", "role", "operation", "outcome"],  # outcome: ok | error | budget
)
MODEL_TOKENS = Counter(
    "sre_agent_model_tokens_total",
    "Model token'ları",
    ["model", "role", "type"],  # input|output|cached_input (önbellekten gelen giriş)
)
MODEL_SECONDS = Histogram(
    "sre_agent_model_request_duration_seconds",
    "Model çağrı süresi",
    ["model", "role", "operation"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)
BUDGET_REJECTIONS = Counter(
    "sre_agent_budget_rejections_total", "Bütçe nedeniyle yapılmayan çağrılar", ["scope"]
)  # scope: koşu | uzman | bağlam
TOOL_RESULTS_TRUNCATED = Counter(
    "sre_agent_tool_results_truncated_total", "~8K sınırında kırpılan tool sonuçları"
)
RUN_TOKENS = Histogram(
    "sre_agent_run_tokens",
    "Koşu başına harcanan token",
    buckets=(1e3, 5e3, 1e4, 5e4, 1e5, 2e5, 4e5, 6e5, 8e5, 1e6),
)
RUN_BUDGET_OUTCOME = Counter(
    "sre_agent_run_budget_outcome_total", "Koşuların bütçe sonucu", ["outcome"]
)  # outcome: normal | wrap_up | exhausted
RUNS_WAITING = Gauge("sre_agent_runs_waiting", "Eşzamanlılık sınırında sırada bekleyen koşular")

# ---- Karar noktaları (T2.5, ADR-028)
DECISIONS = Counter(
    "sre_agent_decisions_total",
    "Karar noktası sonuçları (source: model | rules | similarity; '-gölge' etkili olmayan)",
    ["point", "source", "outcome"],
)

# ---- Olay gruplama (T3.1)
INCIDENT_ASSIGNMENTS = Counter(
    "sre_agent_incident_assignments_total",
    "Raporun olaya atanması",
    ["kind"],  # yeni | katıldı
)
