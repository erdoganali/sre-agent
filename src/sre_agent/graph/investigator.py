"""Investigation graph'ı servise bağlar (T3.2): incident sessizleşince bir kez koşar.

Bir incident'a QUIET süresi boyunca yeni rapor gelmezse graph koşar (node kaybında 6-10 rapor
art arda düşer; hepsinin kanıtı birlikte görülsün). Checkpoint Postgres'te `agent` şemasında,
thread = incident kimliği. Sonuç `reports.incidents.investigation`'a yazılır. Mod shadow'da
Faz 1 raporu etkili kalır; sonuç yalnız kaydedilir.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict

from sre_agent.graph.build import GraphConfig, build
from sre_agent.graph.reasoner import ModelReasoner, Reasoner
from sre_agent.graph.specialists import EvidenceSpecialist, Specialist
from sre_agent.llm.budget import Budget, BudgetedModel
from sre_agent.store import ReportStore

log = logging.getLogger(__name__)
tracer = trace.get_tracer("sre_agent.graph")


class GraphMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"  # graph koşar, sonucu kaydedilir; raporda Faz 1 etkili
    ON = "on"


class InvestigationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: GraphMode = GraphMode.OFF
    quiet_s: float = 180  # incident'a bu kadar yeni rapor gelmeyince koş
    models_file: str = "/etc/sre-agent/models.yaml"
    role: str = "synthesize"  # correlate/evaluate/synthesize'ın model rolü
    specialist_timeout_s: float = 300
    reasoner_timeout_s: float = 180
    # MCP sunucuları (ADR-030): sunucu adı → adres, ör. k8s: http://sre-agent-mcp-k8s:8080/mcp.
    # Boşsa ya da model kataloğu yoksa specialist'ler Faz 1 kanıtıyla çalışır (T3.2).
    mcp_servers: dict[str, str] = {}
    specialist_max_calls: int = 8  # ADR-031


def checkpoint_url(database_url: str) -> str:
    """psycopg bağlantı adresi, arama yolu `agent` şeması (ADR-023: Faz 3 checkpoint'leri)."""
    parts = urlsplit(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    query = "&".join(filter(None, [parts.query, "options=-csearch_path%3Dagent"]))
    return urlunsplit(parts._replace(query=query))


class Investigator:
    def __init__(
        self,
        cfg: InvestigationConfig,
        store: ReportStore,
        reasoner_factory: Callable[[Budget], Reasoner],
        specialist: Specialist | None = None,
        graph_cfg: GraphConfig | None = None,
        database_url: str | None = None,
        specialist_factory: Callable[[Budget], Specialist] | None = None,
    ):
        self.cfg = cfg
        self.store = store
        self.reasoner_factory = reasoner_factory
        self.specialist = specialist or EvidenceSpecialist()
        self.specialist_factory = specialist_factory  # koşu bütçesiyle modelli specialist (T3.4)
        self.graph_cfg = graph_cfg or GraphConfig(
            specialist_timeout_s=cfg.specialist_timeout_s,
            reasoner_timeout_s=cfg.reasoner_timeout_s,
        )
        self.database_url = database_url
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._setup_done = False

    def schedule(self, incident_id: str) -> None:
        """Incident'a rapor geldi: sayaç sıfırlanır, QUIET sonra bir kez koşulur."""
        if self.cfg.mode is GraphMode.OFF:
            return
        if (old := self._timers.get(incident_id)) and not old.done():
            old.cancel()
        self._timers[incident_id] = asyncio.create_task(self._after_quiet(incident_id))

    async def _after_quiet(self, incident_id: str) -> None:
        try:
            await asyncio.sleep(self.cfg.quiet_s)
            await self.run(incident_id)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("investigation koşamadı: %s", incident_id)
        finally:
            if self._timers.get(incident_id) is asyncio.current_task():
                self._timers.pop(incident_id, None)

    async def run(self, incident_id: str) -> dict[str, Any]:
        """Kök span: bir investigation tek trace'tir (adımlar, model ve MCP çağrıları altında)."""
        with tracer.start_as_current_span(
            f"investigation {incident_id}", attributes={"sre_agent.incident.id": incident_id}
        ) as span:
            result = await self._run(incident_id)
            rep = result["report"] or {}
            span.set_attributes(
                {
                    "sre_agent.investigation.category": rep.get("category") or "unknown",
                    "sre_agent.investigation.source": rep.get("source") or "",
                    "sre_agent.investigation.deep_dives": rep.get("deep_dive_count") or 0,
                    "sre_agent.investigation.specialists": result["specialists"] or [],
                    "sre_agent.investigation.tokens": result["budget"].get("used") or 0,
                }
            )
            return result

    async def _run(self, incident_id: str) -> dict[str, Any]:
        reports = await self.store.incident_reports(incident_id)
        if not reports:
            raise LookupError(f"incident yok ya da raporu yok: {incident_id}")
        budget = Budget()
        reasoner = self.reasoner_factory(budget)
        specialist = self.specialist_factory(budget) if self.specialist_factory else self.specialist
        state = {"incident_id": incident_id, "reports": reports}
        # Her koşu yeni thread: aynı incident yeniden koşulursa eski checkpoint üstüne yazılmaz
        thread = f"{incident_id}:{datetime.now(UTC):%Y%m%dT%H%M%S}"
        conf = {"configurable": {"thread_id": thread}}
        if self.database_url:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            async with AsyncPostgresSaver.from_conn_string(
                checkpoint_url(self.database_url)
            ) as saver:
                if not self._setup_done:
                    await saver.setup()
                    self._setup_done = True
                graph = build(specialist, reasoner, budget, self.graph_cfg, saver)
                out = await graph.ainvoke(state, conf)
        else:
            graph = build(specialist, reasoner, budget, self.graph_cfg)
            out = await graph.ainvoke(state, conf)
        result = {
            "thread": thread,
            "report": out.get("report"),
            "correlation": out.get("correlation"),
            "evaluation": out.get("evaluation"),
            "specialists": out.get("specialists"),
            "knowledge": out.get("knowledge", []),
            "findings": out.get("findings"),
            "errors": out.get("errors", []),
            "budget": out.get("budget") or budget.summary(),
        }
        await self.store.save_investigation(
            incident_id, result, str(self.cfg.mode), datetime.now(UTC)
        )
        log.info(
            "investigation bitti: %s → %s (%s)",
            incident_id,
            (out.get("report") or {}).get("category"),
            (out.get("report") or {}).get("source"),
        )
        return result


def model_reasoner_factory(cfg: InvestigationConfig) -> Callable[[Budget], Reasoner] | None:
    """Model kataloğu varsa model reasoner (telemetri + bütçe zinciriyle); yoksa None."""
    path = Path(cfg.models_file)
    if not path.is_file():
        return None
    from sre_agent.adapters.openai_compat import OpenAICompatModel
    from sre_agent.llm.catalog import load_catalog
    from sre_agent.llm.telemetry import InstrumentedModel

    catalog = load_catalog(path)
    adapter = OpenAICompatModel(catalog)

    def factory(budget: Budget) -> Reasoner:
        model = InstrumentedModel(BudgetedModel(adapter, catalog, budget), catalog)
        return ModelReasoner(model, cfg.role)

    return factory


def model_specialist_factory(
    cfg: InvestigationConfig,
    guides: dict[str, str] | None = None,
    connect: Callable[[str], Any] | None = None,
) -> Callable[[Budget], Specialist] | None:
    """Model kataloğu ve MCP adresleri varsa modelli specialist (T3.4, ADR-031); yoksa None.

    `connect` sunucu adından MCP istemcisi üretir; verilmezse `mcp_servers` adresine bağlanılır
    (kayıt ve replay bunu sarar ya da değiştirir, ADR-034).
    """
    path = Path(cfg.models_file)
    if not path.is_file() or not cfg.mcp_servers:
        return None
    from mcp import Client

    from sre_agent.adapters.openai_compat import OpenAICompatModel
    from sre_agent.graph.model_specialist import ModelSpecialist
    from sre_agent.graph.routing import SPECIALISTS
    from sre_agent.llm.catalog import load_catalog
    from sre_agent.llm.telemetry import InstrumentedModel

    catalog = load_catalog(path)
    adapter = OpenAICompatModel(catalog)
    urls = dict(cfg.mcp_servers)
    servers = {name: name for name in SPECIALISTS if name in urls}

    def factory(budget: Budget) -> Specialist:
        base = BudgetedModel(adapter, catalog, budget)
        return ModelSpecialist(
            model_for=lambda scope: InstrumentedModel(base.for_scope(scope), catalog),
            connect=connect or (lambda server: Client(urls[server])),
            servers=servers,
            budget=budget,
            guides=guides,
            max_calls=cfg.specialist_max_calls,
        )

    return factory
