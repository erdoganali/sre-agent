"""Servis için investigator kurulumu (T3.2)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sre_agent.config import AppConfig
from sre_agent.graph.build import GraphConfig
from sre_agent.graph.investigator import (
    GraphMode,
    Investigator,
    model_reasoner_factory,
    model_specialist_factory,
)
from sre_agent.graph.reasoner import Reasoner
from sre_agent.llm.budget import Budget
from sre_agent.packs import CheckRegistry
from sre_agent.store import ReportStore

log = logging.getLogger(__name__)


class NoModel:
    """Model kataloğu yok: her adım hata verir, graph deterministik yedekle rapor üretir."""

    async def correlate(self, *a: object) -> object:
        raise RuntimeError("model kataloğu yok")

    evaluate = correlate
    synthesize = correlate


def build_investigator(
    cfg: AppConfig,
    registry: CheckRegistry,
    store: ReportStore | None,
    database_url: str | None,
    connect: Callable[[str], Any] | None = None,
) -> Investigator | None:
    ic = cfg.investigation
    if ic.mode is GraphMode.OFF:
        return None
    if store is None:
        log.warning("investigation için kalıcı depo gerekir; kapalı")
        return None
    factory = model_reasoner_factory(ic)
    if factory is None:
        log.warning("model kataloğu yok (%s): investigation yalnız yedek yolla", ic.models_file)

        def factory(_: Budget) -> Reasoner:
            return NoModel()  # type: ignore[return-value]

    kb: Callable[[str], Awaitable[list[Any]]] | None = None
    if cfg.knowledge.uri:
        from sre_agent.adapters.vector_store import KnowledgeBase

        base = KnowledgeBase(cfg.knowledge)
        top_k = cfg.knowledge.top_k

        async def search(query: str) -> list[Any]:
            return await base.search(query, top_k, "failure")

        kb = search

    graph_cfg = GraphConfig(
        specialist_timeout_s=ic.specialist_timeout_s,
        reasoner_timeout_s=ic.reasoner_timeout_s,
        product_checks=registry.product_checks,
        product_aliases=registry.product_aliases,
        knowledge=kb,
    )
    specialists = model_specialist_factory(ic, registry.specialist_guides, connect)
    if specialists is None:
        log.info(
            "modelli specialist kapalı (katalog ya da mcp_servers yok): Faz 1 kanıtı kullanılır"
        )
    return Investigator(
        ic,
        store,
        factory,
        graph_cfg=graph_cfg,
        database_url=database_url,
        specialist_factory=specialists,
    )
