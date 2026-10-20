"""Kontrol çerçevesi: Check arayüzü, Evidence modeli ve paralel koşucu.

Kontroller LLM'siz ve salt okunurdur. Her kontrolün zaman aşımı vardır; hata veren ya da
zaman aşımına uğrayan kontrol `error` olarak işaretlenir ve rapor yine üretilir.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from sre_agent.ports import (
    Capability,
    ChangePort,
    K8sPort,
    LogsPort,
    MetricsPort,
    TlsPort,
    TracesPort,
)
from sre_agent.taxonomy import load as load_taxonomy


class Status(StrEnum):
    OK = "ok"
    ANOMALY = "anomaly"
    NO_DATA = "no_data"  # veri yok: "sorun yok" ile karıştırılmaz
    ERROR = "error"  # kontrol çalışmadı (istisna, zaman aşımı, eksik adaptör)


class Window(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _ordered(self) -> Window:
        if self.end <= self.start:
            raise ValueError("pencere bitişi başlangıçtan sonra olmalı")
        return self


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    check: str
    capability: Capability
    categories: tuple[str, ...]
    status: Status
    summary: str
    data: JsonValue = None
    query: str | None = None
    window: Window
    duration_ms: float = 0.0
    error: str | None = None


class CheckContext(BaseModel):
    """Bir kontrol koşusunun girdisi: alarm etiketleri, pencere ve adaptörler."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    labels: Mapping[str, str] = Field(default_factory=dict)
    window: Window
    ports: Mapping[Capability, Any] = Field(default_factory=dict)

    def metrics(self) -> MetricsPort:
        return self._port(Capability.METRICS, MetricsPort)  # type: ignore[type-abstract]

    def logs(self) -> LogsPort:
        return self._port(Capability.LOGS, LogsPort)  # type: ignore[type-abstract]

    def traces(self) -> TracesPort:
        return self._port(Capability.TRACES, TracesPort)  # type: ignore[type-abstract]

    def k8s(self) -> K8sPort:
        return self._port(Capability.K8S, K8sPort)  # type: ignore[type-abstract]

    def changes(self) -> ChangePort:
        return self._port(Capability.CHANGE, ChangePort)  # type: ignore[type-abstract]

    def tls(self) -> TlsPort:
        return self._port(Capability.TLS, TlsPort)  # type: ignore[type-abstract]

    def _port[P](self, cap: Capability, proto: type[P]) -> P:
        port = self.ports.get(cap)
        if port is None:
            raise MissingPortError(cap)
        if not isinstance(port, proto):
            raise TypeError(f"{cap} adaptörü {proto.__name__} arayüzünü uygulamıyor")
        return port


class MissingPortError(LookupError):
    def __init__(self, capability: Capability) -> None:
        super().__init__(f"{capability} yeteneği için adaptör yok")
        self.capability = capability


class Check(ABC):
    """Tek bir deterministik kontrol. Alt sınıflar `run`'ı uygular."""

    name: ClassVar[str]
    capability: ClassVar[Capability]
    categories: ClassVar[tuple[str, ...]]
    timeout_s: ClassVar[float] = 20.0
    # Varsayılan sorgular (ör. PromQL). Ortama özel olanlar kurucuda değiştirilir (T1.7).
    queries: ClassVar[Mapping[str, str]] = {}

    def __init__(self, queries: Mapping[str, str] | None = None) -> None:
        unknown = set(queries or {}) - set(self.queries)
        if unknown:
            raise ValueError(f"{self.name}: bilinmeyen sorgu anahtarları {sorted(unknown)}")
        self.q: dict[str, str] = {**self.queries, **(queries or {})}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Soyut sınıflar ve ortak ara sınıflar (gövdesinde `_base = True`) doğrulanmaz.
        if getattr(cls, "__abstractmethods__", None) or cls.__dict__.get("_base", False):
            return
        known = {c.id for c in load_taxonomy().categories}
        categories = getattr(cls, "categories", ())
        unknown = set(categories) - known
        if not categories or unknown:
            raise TypeError(f"{cls.__name__}: geçersiz kategoriler {sorted(unknown) or '()'}")

    @abstractmethod
    async def run(self, ctx: CheckContext) -> Evidence: ...

    def evidence(
        self,
        ctx: CheckContext,
        status: Status,
        summary: str,
        *,
        data: Any = None,  # Evidence.data (JsonValue) çalışma anında doğrular
        query: str | None = None,
    ) -> Evidence:
        return Evidence(
            check=self.name,
            capability=self.capability,
            categories=self.categories,
            status=status,
            summary=summary,
            data=data,
            query=query,
            window=ctx.window,
        )


async def _run_one(check: Check, ctx: CheckContext) -> Evidence:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(check.run(ctx), timeout=check.timeout_s)
    except TimeoutError:
        result = check.evidence(ctx, Status.ERROR, "kontrol çalışmadı: zaman aşımı").model_copy(
            update={"error": f"{check.timeout_s} sn zaman aşımı"}
        )
    except MissingPortError as exc:
        result = check.evidence(ctx, Status.ERROR, "kontrol çalışmadı: adaptör yok").model_copy(
            update={"error": str(exc)}
        )
    except Exception as exc:
        result = check.evidence(ctx, Status.ERROR, "kontrol çalışmadı: hata").model_copy(
            update={"error": f"{type(exc).__name__}: {exc}"}
        )
    return result.model_copy(update={"duration_ms": (time.perf_counter() - started) * 1000})


async def run_checks(checks: Sequence[Check], ctx: CheckContext) -> list[Evidence]:
    """Kontrolleri paralel koşar; sonuçlar kontrollerin verildiği sırayla döner."""
    return list(await asyncio.gather(*(_run_one(c, ctx) for c in checks)))
