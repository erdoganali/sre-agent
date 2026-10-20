from datetime import timedelta
from typing import Any

import httpx

from fakes import END, FakeK8s, FakeMetrics
from sre_agent.config import AppConfig
from sre_agent.freshness import (
    FreshnessProbe,
    SourceState,
    SourceStatus,
    assess,
    blind_spot_reasons,
)
from sre_agent.packs import CheckRegistry
from sre_agent.ports import Capability, Sample
from sre_agent.report import annotation_text, to_markdown
from sre_agent.service import metrics
from sre_agent.service.app import create_app
from sre_agent.service.runner import ReportRunner

PROBE = FreshnessProbe(name="milvus", query='age{{ns="{ns}"}}', max_age_s=600)


class Broken:
    async def query_range(self, *a: Any) -> list[Any]:
        raise ConnectionError("loki kapalı")


def age(value: float | None) -> FakeMetrics:
    def resolve(q: str) -> list[Sample]:
        if q == "vector(1)":
            return [Sample({}, 1)]
        return [] if value is None else [Sample({}, value)]

    return FakeMetrics(resolver=resolve)


async def test_assess_fresh_stale_unreachable_and_not_applicable() -> None:
    ports = {Capability.METRICS: age(1200), Capability.LOGS: Broken(), Capability.K8S: FakeK8s()}
    needed = {Capability.METRICS, Capability.LOGS, Capability.K8S, Capability.TRACES}
    out = {s.name: s for s in await assess(ports, needed, [PROBE], {"namespace": "acme"}, END)}
    assert out["metrics"].state is SourceState.FRESH
    assert out["k8s"].state is SourceState.FRESH
    assert out["logs"].state is SourceState.UNREACHABLE and "loki kapalı" in (
        out["logs"].detail or ""
    )
    assert out["traces"].state is SourceState.UNREACHABLE  # adaptör yok
    assert out["milvus"].state is SourceState.STALE and out["milvus"].age_s == 1200

    fresh = await assess(
        {Capability.METRICS: age(30)}, {Capability.METRICS}, [PROBE], {"namespace": "acme"}, END
    )
    assert fresh[-1].state is SourceState.FRESH
    other_ns = await assess(
        {Capability.METRICS: age(None)}, {Capability.METRICS}, [PROBE], {"namespace": "x"}, END
    )
    assert other_ns[-1].state is SourceState.NOT_APPLICABLE
    no_ns = await assess({Capability.METRICS: age(30)}, {Capability.METRICS}, [PROBE], {}, END)
    assert no_ns[-1].state is SourceState.NOT_APPLICABLE


def test_blind_spot_reasons() -> None:
    sources = [
        SourceStatus(name="metrics", state=SourceState.FRESH),
        SourceStatus(name="milvus", state=SourceState.STALE, age_s=1200),
        SourceStatus(name="logs", state=SourceState.UNREACHABLE),
    ]
    reasons = blind_spot_reasons(sources, ["ok", "no_data", "error"])
    assert reasons == [
        "milvus: son veri 1200 sn önce",
        "logs: erişilemiyor",
        "2/3 kontrol veri yok ya da hata",
    ]
    assert blind_spot_reasons(sources[:1], ["ok", "no_data"]) == []


def runner(metrics_port: FakeMetrics) -> ReportRunner:
    cfg = AppConfig(default_checks=["kafka_consumer_lag"], freshness=[PROBE])
    return ReportRunner(cfg, CheckRegistry(), {Capability.METRICS: metrics_port}, now=lambda: END)


def alert(name: str = "X") -> dict[str, Any]:
    return {
        "alerts": [
            {
                "status": "firing",
                "fingerprint": name,
                "labels": {"alertname": name, "namespace": "acme"},
                "startsAt": (END - timedelta(minutes=1)).isoformat(),
            }
        ]
    }


async def test_stale_telemetry_marks_report_as_blind_spot() -> None:
    r = runner(age(1800))
    r.submit(alert())
    await r.wait_idle()
    (report,) = r.reports.values()
    assert report.blind_spots and "milvus: son veri 1800 sn önce" in report.blind_spots
    md = to_markdown(report)
    assert "⚠ **Kör nokta:**" in md and "## Veri kaynakları" in md
    assert annotation_text(report, None).startswith("⚠ kör nokta (")


async def test_watchdog_is_recorded_not_reported_and_metrics_exposed() -> None:
    r = runner(age(30))
    before = metrics.ALERTS.labels(status="firing")._value.get()
    assert r.submit(alert("Watchdog")) == []
    assert metrics.WATCHDOG_LAST_SEEN._value.get() == END.timestamp()
    r.submit(alert("Real"))
    await r.wait_idle()
    assert len(r.reports) == 1 and not next(iter(r.reports.values())).blind_spots
    assert metrics.ALERTS.labels(status="firing")._value.get() == before + 1
    app = create_app(r)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        body = (await c.get("/metrics")).text
    assert (
        "sre_agent_reports_total" in body
        and "sre_agent_watchdog_last_seen_timestamp_seconds" in body
    )
    assert 'sre_agent_check_results_total{check="kafka_consumer_lag"' in body
