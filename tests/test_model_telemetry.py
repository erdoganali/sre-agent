"""Model telemetrisi (T2.3): span özellikleri ve Prometheus metrikleri."""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

from sre_agent.llm import telemetry
from sre_agent.llm.budget import Budget, BudgetedModel, Limits
from sre_agent.llm.catalog import Catalog
from sre_agent.llm.telemetry import InstrumentedModel, record_run_budget
from sre_agent.ports.model import Completion, Message, ModelError, Usage

CAT = Catalog.model_validate(
    {
        "models": {
            "q": {
                "name": "qwen-x",
                "url": "http://gw:8080/v1/chat/completions",
                "max_context_tokens": 10_000,
            }
        },
        "roles": {"decision": {"model": "q", "max_tokens": 100}},
    }
)
SECRET = "GİZLİ-LOG-İÇERİĞİ"


class Fake:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail

    async def complete(self, role: str, messages: list[Message], **_: Any) -> Completion:
        if self.fail:
            raise self.fail
        return Completion("ok", "q", Usage(30, 7), 0.1, reasoning="düşündüm", finish_reason="stop")

    async def complete_typed(
        self, role: str, messages: list[Message], schema: Any, **_: Any
    ) -> Any:
        raise NotImplementedError

    async def classify(self, role: str, messages: list[Message], labels: list[str]) -> Any:
        raise NotImplementedError


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("test"))
    return exporter


def value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_span_follows_genai_conventions_without_content(spans: InMemorySpanExporter) -> None:
    before = value("sre_agent_model_tokens_total", model="q", role="decision", type="input")
    m = InstrumentedModel(Fake(), CAT)
    await m.complete("decision", [Message("user", SECRET)])
    (span,) = spans.get_finished_spans()
    a = dict(span.attributes or {})
    assert span.name == "chat qwen-x"
    assert a["gen_ai.operation.name"] == "chat" and a["gen_ai.request.model"] == "qwen-x"
    assert a["gen_ai.usage.input_tokens"] == 30 and a["gen_ai.usage.output_tokens"] == 7
    assert a["gen_ai.request.max_tokens"] == 100 and a["server.address"] == "gw:8080"
    assert a["sre_agent.model.role"] == "decision" and a["sre_agent.model.reasoning"] is True
    assert SECRET not in str(a)  # içerik span'a yazılmaz
    assert (
        value("sre_agent_model_tokens_total", model="q", role="decision", type="input")
        == before + 30
    )
    assert (
        value(
            "sre_agent_model_requests_total",
            model="q",
            role="decision",
            operation="complete",
            outcome="ok",
        )
        >= 1
    )


async def test_errors_and_budget_rejections_are_counted(spans: InMemorySpanExporter) -> None:
    err0 = value(
        "sre_agent_model_requests_total",
        model="q",
        role="decision",
        operation="complete",
        outcome="error",
    )
    with pytest.raises(ModelError):
        await InstrumentedModel(Fake(ModelError("erişilemedi")), CAT).complete(
            "decision", [Message("user", "x")]
        )
    assert (
        value(
            "sre_agent_model_requests_total",
            model="q",
            role="decision",
            operation="complete",
            outcome="error",
        )
        == err0 + 1
    )

    rej0 = value("sre_agent_budget_rejections_total", scope="koşu")
    budget = Budget(limits=Limits(run_hard=50))
    wrapped = InstrumentedModel(BudgetedModel(Fake(), CAT, budget), CAT)
    with pytest.raises(Exception, match="bütçe"):
        await wrapped.complete("decision", [Message("user", "x")])
    assert value("sre_agent_budget_rejections_total", scope="koşu") == rej0 + 1
    assert spans.get_finished_spans()[-1].attributes["error.type"] == "budget_exceeded"  # type: ignore[index]


def test_run_budget_outcome() -> None:
    before = value("sre_agent_run_budget_outcome_total", outcome="wrap_up")
    b = Budget(limits=Limits(run_soft=10))
    b.used = 20
    b.truncated_tool_results = 2
    t0 = value("sre_agent_tool_results_truncated_total")
    record_run_budget(b)
    assert value("sre_agent_run_budget_outcome_total", outcome="wrap_up") == before + 1
    assert value("sre_agent_tool_results_truncated_total") == t0 + 2


def test_tracing_off_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert telemetry.setup_tracing() is False
    assert trace.get_tracer_provider() is not None
