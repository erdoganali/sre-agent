"""Veri tazeliği: rapor, dayandığı kaynakların erişilebilir ve güncel olup olmadığını yazar.

Hat sessizce koparsa kontroller `no_data` döner ve rapor "normal" gibi okunabilir; bu modül
"kör nokta"yı görünür kılar. Tazelik sorguları ortama özgüdür (yapılandırmada).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from sre_agent.ports import Capability

TIMEOUT_S = 10.0


class SourceState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNREACHABLE = "unreachable"
    NOT_APPLICABLE = "n/a"  # ör. sorgu bu namespace için sonuç döndürmüyor


class FreshnessProbe(BaseModel):
    """Yapılandırmadaki tazelik sorgusu: sonuç son örneğin yaşı (sn); boşsa uygulanmaz."""

    model_config = ConfigDict(extra="forbid")

    name: str
    query: str  # PromQL, {ns} yer tutucusu; tek değer = yaş (sn)
    max_age_s: float


class SourceStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    state: SourceState
    age_s: float | None = None
    detail: str | None = None


async def _reachable(name: str, call: Any) -> SourceStatus:
    try:
        await asyncio.wait_for(call, TIMEOUT_S)
    except Exception as exc:
        return SourceStatus(
            name=name, state=SourceState.UNREACHABLE, detail=f"{type(exc).__name__}: {exc}"[:200]
        )
    return SourceStatus(name=name, state=SourceState.FRESH)


async def _probe(p: FreshnessProbe, metrics: Any, ns: str | None, at: datetime) -> SourceStatus:
    if "{ns}" in p.query and not ns:
        return SourceStatus(name=p.name, state=SourceState.NOT_APPLICABLE, detail="namespace yok")
    try:
        result = await asyncio.wait_for(metrics.query(p.query.format(ns=ns), at), TIMEOUT_S)
    except Exception as exc:
        return SourceStatus(
            name=p.name, state=SourceState.UNREACHABLE, detail=f"{type(exc).__name__}: {exc}"[:200]
        )
    if not result:
        return SourceStatus(name=p.name, state=SourceState.NOT_APPLICABLE, detail="veri yok")
    age = float(result[0].value)
    state = SourceState.FRESH if age <= p.max_age_s else SourceState.STALE
    return SourceStatus(
        name=p.name, state=state, age_s=round(age), detail=f"eşik {p.max_age_s:.0f} sn"
    )


async def assess(
    ports: Mapping[Capability, Any],
    needed: set[Capability],
    probes: list[FreshnessProbe],
    labels: Mapping[str, str],
    at: datetime,
) -> list[SourceStatus]:
    """Raporun kullandığı yeteneklerin erişilebilirliği + yapılandırmadaki tazelik sorguları."""
    tasks: list[Any] = []
    for cap in sorted(needed, key=str):
        port = ports.get(cap)
        if port is None:
            tasks.append(
                asyncio.sleep(
                    0,
                    SourceStatus(
                        name=str(cap), state=SourceState.UNREACHABLE, detail="adaptör yok"
                    ),
                )
            )
        elif cap is Capability.METRICS:
            tasks.append(_reachable("metrics", port.query("vector(1)", at)))
        elif cap is Capability.LOGS:
            since = at - timedelta(minutes=5)
            query = port.query_range('{service_name=~".+"}', since, at, 1)
            tasks.append(_reachable("logs", query))
        elif cap is Capability.TRACES:
            since = at - timedelta(minutes=5)
            tasks.append(_reachable("traces", port.search("{}", since, at, 1)))
        elif cap is Capability.K8S:
            # Namespace kapsamlı yetkide (RBAC scope=namespaces) Namespace listelenemez;
            # alarmın namespace'i varsa oradaki pod'lar denenir.
            ns = labels.get("namespace")
            call = port.list("Pod", namespace=ns) if ns else port.list("Namespace")
            tasks.append(_reachable("k8s", call))
    metrics = ports.get(Capability.METRICS)
    if metrics is not None and Capability.METRICS in needed:
        tasks.extend(_probe(p, metrics, labels.get("namespace"), at) for p in probes)
    return list(await asyncio.gather(*tasks))


def blind_spot_reasons(sources: list[SourceStatus], statuses: list[str]) -> list[str]:
    """Kör nokta: erişilemeyen/bayat kaynak ya da kontrollerin yarısından fazlası veri yok/hata."""
    reasons: list[str] = []
    for s in sources:
        if s.state is SourceState.UNREACHABLE:
            reasons.append(f"{s.name}: erişilemiyor")
        elif s.state is SourceState.STALE:
            reasons.append(f"{s.name}: son veri {s.age_s:.0f} sn önce")
    blind = sum(1 for st in statuses if st in ("no_data", "error"))
    if statuses and blind * 2 > len(statuses):
        reasons.append(f"{blind}/{len(statuses)} kontrol veri yok ya da hata")
    return reasons
