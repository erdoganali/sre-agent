"""Model çağrılarının telemetrisi (T2.3): Prometheus metrikleri ve OTel GenAI span'ları.

Span'lar OpenTelemetry GenAI semantic conventions'a uyar (`gen_ai.*`). Prompt ve cevap
içeriği span'a yazılmaz: log/rapor içeriği güvenilmez ve kişisel veri taşıyabilir.
OTel yapılandırılmamışsa (`OTEL_EXPORTER_OTLP_ENDPOINT` yoksa) tracer işlem yapmaz.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from sre_agent.llm.budget import Budget, BudgetExceeded
from sre_agent.llm.catalog import Catalog
from sre_agent.ports.model import (
    Classification,
    Completion,
    Message,
    ModelPort,
    T,
    ToolSpec,
    ToolTurn,
    Typed,
)
from sre_agent.service import metrics

tracer = trace.get_tracer("sre_agent.llm")
R = TypeVar("R")


def setup_tracing(service_name: str = "sre-agent") -> bool:
    """OTLP/HTTP dışa aktarımı; uç nokta ortam değişkeninden (yoksa kurulmaz)."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": os.environ.get("OTEL_SERVICE_NAME", service_name)}
        )
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return True


def _completion_of(result: Any) -> Completion:
    if isinstance(result, Completion):
        return result
    return result.completion  # type: ignore[no-any-return]


class InstrumentedModel:
    """Model portunu sarar; her çağrı için span ve metrik üretir. Bütçe sarmalayıcısının dışına
    takılır ki bütçe reddi de görünsün: Instrumented(Budgeted(adaptör))."""

    def __init__(self, inner: ModelPort, catalog: Catalog):
        self.inner = inner
        self.catalog = catalog

    async def _observe(
        self,
        operation: str,
        role: str,
        max_tokens: int | None,
        call: Callable[[], Awaitable[R]],
    ) -> R:
        key, spec, r = self.catalog.resolve(role)
        attrs: dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "openai",  # API biçimi (OpenAI uyumlu); sağlayıcı değil
            "gen_ai.request.model": spec.name,
            "sre_agent.model.key": key,
            "sre_agent.model.role": role,
            "sre_agent.model.operation": operation,
            "server.address": spec.url.split("/")[2] if "://" in spec.url else spec.url,
        }
        if max_tokens or r.max_tokens:
            attrs["gen_ai.request.max_tokens"] = max_tokens or r.max_tokens
        t0 = time.monotonic()
        with tracer.start_as_current_span(f"chat {spec.name}", attributes=attrs) as span:
            try:
                result = await call()
            except BudgetExceeded as exc:
                scope = exc.scope.split(":")[0]
                metrics.BUDGET_REJECTIONS.labels(scope=scope).inc()
                metrics.MODEL_REQUESTS.labels(key, role, operation, "budget").inc()
                span.set_attribute("error.type", "budget_exceeded")
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            except Exception as exc:
                metrics.MODEL_REQUESTS.labels(key, role, operation, "error").inc()
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
                raise
            finally:
                metrics.MODEL_SECONDS.labels(key, role, operation).observe(time.monotonic() - t0)
            c = _completion_of(result)
            metrics.MODEL_REQUESTS.labels(key, role, operation, "ok").inc()
            metrics.MODEL_TOKENS.labels(key, role, "input").inc(c.usage.prompt_tokens)
            metrics.MODEL_TOKENS.labels(key, role, "output").inc(c.usage.completion_tokens)
            if c.usage.cached_tokens:  # ortak önek önbellekten (T3.4)
                metrics.MODEL_TOKENS.labels(key, role, "cached_input").inc(c.usage.cached_tokens)
            span.set_attribute("gen_ai.usage.cache_read.input_tokens", c.usage.cached_tokens)
            span.set_attribute("gen_ai.response.model", c.model)
            span.set_attribute("gen_ai.usage.input_tokens", c.usage.prompt_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", c.usage.completion_tokens)
            if c.finish_reason:
                span.set_attribute("gen_ai.response.finish_reasons", [c.finish_reason])
            span.set_attribute("sre_agent.model.reasoning", c.reasoning is not None)
            return result

    async def complete(
        self,
        role: str,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Completion:
        return await self._observe(
            "complete",
            role,
            max_tokens,
            lambda: self.inner.complete(
                role, messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort
            ),
        )

    async def complete_typed(
        self,
        role: str,
        messages: list[Message],
        schema: type[T],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Typed[T]:
        return await self._observe(
            "typed",
            role,
            max_tokens,
            lambda: self.inner.complete_typed(
                role, messages, schema, max_tokens=max_tokens, reasoning_effort=reasoning_effort
            ),
        )

    async def complete_tools(
        self,
        role: str,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int | None = None,
    ) -> ToolTurn:
        inner: Any = self.inner  # ToolModelPort (adaptör ve bütçe sarmalayıcısı uygular)
        return await self._observe(
            "tools",
            role,
            max_tokens,
            lambda: inner.complete_tools(role, messages, tools, max_tokens=max_tokens),
        )

    async def classify(
        self, role: str, messages: list[Message], labels: list[str]
    ) -> Classification:
        return await self._observe(
            "classify", role, 1, lambda: self.inner.classify(role, messages, labels)
        )


def record_run_budget(budget: Budget) -> None:
    """Koşu bitince çağrılır: koşunun toplam token'ı ve bütçe sonucu."""
    metrics.RUN_TOKENS.observe(budget.used)
    outcome = "exhausted" if budget.exhausted else "wrap_up" if budget.wrap_up else "normal"
    metrics.RUN_BUDGET_OUTCOME.labels(outcome=outcome).inc()
    if budget.truncated_tool_results:
        metrics.TOOL_RESULTS_TRUNCATED.inc(budget.truncated_tool_results)
