"""Servis için karar fonksiyonu: katalog → adaptör → bütçe → telemetri → motor."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sre_agent.adapters.openai_compat import OpenAICompatModel
from sre_agent.decisions import DecisionConfig, DecisionSet, Mode
from sre_agent.decisions.engine import DecisionEngine
from sre_agent.llm.budget import Budget, BudgetedModel
from sre_agent.llm.catalog import load_catalog
from sre_agent.llm.telemetry import InstrumentedModel, record_run_budget
from sre_agent.report import Report
from sre_agent.taxonomy import load as load_taxonomy

log = logging.getLogger(__name__)

Decider = Callable[[Report, list[dict[str, Any]]], Awaitable[DecisionSet]]


def build_decider(cfg: DecisionConfig) -> Decider | None:
    """`off` ise None. Katalog yoksa ya da okunamazsa yalnız kurallarla karar verilir."""
    if cfg.mode is Mode.OFF:
        return None
    taxonomy = load_taxonomy()
    path = Path(cfg.models_file)
    if not path.is_file():
        log.warning("model kataloğu yok (%s): kararlar yalnız kurallarla", path)
        rules_only = DecisionEngine(cfg.model_copy(update={"mode": Mode.SHADOW}), taxonomy, None)

        async def by_rules(report: Report, history: list[dict[str, Any]]) -> DecisionSet:
            return await rules_only.assess(report, history)

        return by_rules
    catalog = load_catalog(path)
    adapter = OpenAICompatModel(catalog)  # yetenek bilgisi koşular arasında korunur

    async def decide(report: Report, history: list[dict[str, Any]]) -> DecisionSet:
        budget = Budget()  # karar başına ayrı hesap
        model = InstrumentedModel(BudgetedModel(adapter, catalog, budget), catalog)
        try:
            return await DecisionEngine(cfg, taxonomy, model).assess(report, history)
        finally:
            record_run_budget(budget)

    return decide
