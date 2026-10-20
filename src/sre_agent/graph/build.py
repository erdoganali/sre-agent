"""Investigation graph'ın kurulumu (LangGraph).

gather → (Send) specialist'ler → correlate → evaluate → routing (kod) → deep dive ↺ | synthesize

Her graph node'u kendi zaman aşımıyla koşar; aşılırsa ya da model hata verirse o adımın
deterministik yedeği kullanılır, graph durmaz. Kontrol akışı yalnız routing.py'deki saf
fonksiyonlarla belirlenir (ADR-001).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from sre_agent import incidents, knowledge
from sre_agent.graph import routing
from sre_agent.graph.reasoner import (
    Reasoner,
    as_findings,
    fallback_correlation,
    fallback_evaluation,
    fallback_report,
    passthrough,
)
from sre_agent.graph.schemas import Correlation, Evaluation, Finding
from sre_agent.graph.specialists import Specialist
from sre_agent.graph.state import GraphState
from sre_agent.llm.budget import Budget

log = logging.getLogger(__name__)
tracer = trace.get_tracer("sre_agent.graph")


@dataclass
class GraphConfig:
    specialist_timeout_s: float = 300
    reasoner_timeout_s: float = 180
    product_checks: frozenset[str] = field(default_factory=frozenset)  # paket kontrolleri
    # Modelin ürün specialist'i için kullanabileceği adlar (paket adı, ürün bileşenleri)
    product_aliases: frozenset[str] = field(default_factory=frozenset)
    # Bilgi tabanı araması (RAG): sorgu → isabetler; yoksa brief'e bilinen arıza eklenmez
    knowledge: Callable[[str], Awaitable[list[Any]]] | None = None
    knowledge_timeout_s: float = 10


async def _guard[R](
    what: str, coro: Awaitable[R], timeout: float, fallback: Callable[[], R]
) -> tuple[R, str | None]:
    """Adımı zaman aşımıyla koşar; hata ya da aşımda yedeğe düşer ve hatayı döndürür.

    Her adım investigation trace'inde bir span'dır: içindeki model ve MCP çağrıları altına düşer.
    """
    with tracer.start_as_current_span(what) as span:
        try:
            return await asyncio.wait_for(coro, timeout), None
        except Exception as exc:  # model, ağ, şema, zaman aşımı: hepsi yedeğe
            msg = f"{what}: {type(exc).__name__}: {str(exc)[:200]}"
            log.warning("graph adımı yedeğe düştü — %s", msg)
            span.set_status(Status(StatusCode.ERROR, msg))
            span.set_attribute("sre_agent.fallback", True)
            return fallback(), msg


def gather_state(
    incident_id: str, reports: list[dict[str, Any]], product_checks: frozenset[str]
) -> dict[str, Any]:
    """Kod: incident'ın raporlarından brief, yetenek başına kanıt ve kural kategorisi."""
    evidence: dict[str, list[dict[str, Any]]] = {name: [] for name in routing.SPECIALISTS}
    seen: set[tuple[str, str]] = set()
    cats: Counter[str] = Counter()
    lost: set[str] = set()
    namespaces: set[str] = set()
    alerts: Counter[str] = Counter()
    from_pack = False  # bir ürün paketinin alarmı: paket eşledi ya da paket kontrolleri koştu
    for rep in reports:
        alerts[rep["alert"]["name"]] += 1
        from_pack = (
            from_pack
            or bool(rep.get("packs"))
            or any(e.get("check") in product_checks for e in rep.get("evidence", []))
        )
        if ns := rep["alert"]["labels"].get("namespace"):
            namespaces.add(ns)
        for c, n in (rep.get("anomalies_by_category") or {}).items():
            cats[c] += n
        for e in rep.get("evidence", []):
            if e.get("status") != "anomaly":
                continue
            if e["check"] == incidents.NODE_CHECK and isinstance(e.get("data"), list):
                lost |= {str(r.get("node")) for r in e["data"] if isinstance(r, dict)}
            name = (
                "product"
                if e["check"] in product_checks
                else next(
                    (s for s, caps in routing.SPECIALISTS.items() if e.get("capability") in caps),
                    "metrics",
                )
            )
            key = (name, f"{e['check']}:{e.get('summary', '')[:80]}")
            if key not in seen:  # aynı kanıt birçok raporda tekrar eder
                seen.add(key)
                evidence[name].append(
                    {
                        "check": e["check"],
                        "summary": e.get("summary", ""),
                        "categories": e.get("categories", []),
                    }
                )
    node_loss = bool(lost) or any(
        e["check"] in ("node_events", "node_conditions") for ev in evidence.values() for e in ev
    )
    starts = sorted(
        str(r["alert"].get("starts_at") or "") for r in reports if r["alert"].get("starts_at")
    )
    when = (
        f"Zaman (UTC): ilk alarm {starts[0]}, son alarm {starts[-1]}. Araçlarda olay anı olarak "
        "bu zamanı kullan (at/end parametreleri)."
        if starts
        else "Zaman: bilinmiyor"
    )
    brief = "\n".join(
        [
            f"Incident {incident_id}: {len(reports)} rapor, alarmlar: "
            + ", ".join(f"{a} ({n})" for a, n in alerts.most_common()),
            when,
            f"Namespace: {', '.join(sorted(namespaces)) or '-'}",
            f"Kayıp node: {', '.join(sorted(lost)) or 'yok'}",
            "Anomali gören kontroller: "
            + "; ".join(
                f"{k}: {', '.join(e['check'] for e in v)}" for k, v in evidence.items() if v
            ),
        ]
    )
    return {
        "incident_id": incident_id,
        "brief": brief,
        "evidence": evidence,
        "rule_category": cats.most_common(1)[0][0] if cats else None,
        "specialists": routing.select_specialists(evidence, node_loss, from_pack),
        "deep_dive_round": 0,
        "deep_dive_tasks": [],
        "errors": [],
    }


def build(
    specialist: Specialist,
    reasoner: Reasoner,
    budget: Budget,
    cfg: GraphConfig | None = None,
    checkpointer: Any = None,
) -> Any:
    cfg = cfg or GraphConfig()

    async def gather(state: GraphState) -> dict[str, Any]:
        out = gather_state(state["incident_id"], state["reports"], cfg.product_checks)
        if cfg.knowledge is None:
            return out
        # Sorgu kodda kurulur: alarm adları + anomali gören kontroller (model karar vermez)
        alerts = {r["alert"]["name"] for r in state["reports"]}
        checks = {e["check"] for ev in out["evidence"].values() for e in ev}
        query = " ".join(sorted(alerts) + sorted(checks))
        try:
            hits = await asyncio.wait_for(cfg.knowledge(query), cfg.knowledge_timeout_s)
        except Exception as exc:  # bilgi tabanı yoksa graph yine koşar
            out["errors"] = [f"knowledge: {type(exc).__name__}: {str(exc)[:200]}"]
            return out
        failures = [h for h in hits if h.doc.kind == "failure"]
        if failures:
            out["brief"] += "\n" + knowledge.brief_section(failures)
            out["knowledge"] = [
                {"id": h.doc.id, "title": h.doc.title, "category": h.doc.category, "score": h.score}
                for h in failures
            ]
        return out

    def dispatch(state: GraphState) -> list[Send]:
        return [
            Send(
                "run_specialist",
                {
                    "name": n,
                    "question": None,
                    "brief": state["brief"],
                    "evidence": state["evidence"].get(n, []),
                },
            )
            for n in state["specialists"]
        ]

    def dispatch_deep_dive(state: GraphState) -> list[Send]:
        return [
            Send(
                "run_specialist",
                {
                    "name": t["specialist"],
                    "question": t["question"],
                    "brief": state["brief"],
                    "evidence": state["evidence"].get(t["specialist"], []),
                },
            )
            for t in state["deep_dive_tasks"]
        ]

    async def run_specialist(task: dict[str, Any]) -> dict[str, Any]:
        name = task["name"]
        finding, err = await _guard(
            f"specialist {name}",
            specialist.run(name, task["brief"], task["evidence"], task["question"]),
            cfg.specialist_timeout_s,
            lambda: Finding(specialist=name, summary="çalışamadı", error="hata"),
        )
        out = finding.model_dump() | {"deep_dive": task["question"] is not None}
        return {"findings": {name: out}, "errors": [err] if err else []}

    async def correlate(state: GraphState) -> dict[str, Any]:
        findings = as_findings(state.get("findings"))
        usable = {n: f for n, f in findings.items() if not f.error}
        if len(usable) == 1:  # tek specialist: LLM çağrılmaz
            return {"correlation": passthrough(usable).model_dump()}
        corr, err = await _guard(
            "correlate",
            reasoner.correlate(state["brief"], usable),
            cfg.reasoner_timeout_s,
            lambda: fallback_correlation(usable),
        )
        if corr.method == "model":
            # Destekçiler kodda: kategoriyi en güçlü adayı olarak gösteren specialist'ler (yedek
            # oylamayla aynı ölçüt). Model bu alanı boş bırakabiliyor; listenin herhangi bir
            # yerinde geçmek de yetmez (kapasite neredeyse her listede var).
            corr = corr.model_copy(
                update={
                    "supporting": sorted(
                        n for n, f in usable.items() if f.suspects[:1] == [corr.category]
                    )
                }
            )
        return {"correlation": corr.model_dump(), "errors": [err] if err else []}

    async def evaluate(state: GraphState) -> dict[str, Any]:
        findings = as_findings(state.get("findings"))
        corr = Correlation.model_validate(state["correlation"])
        if corr.method in ("passthrough", "fallback"):
            ev: Evaluation = fallback_evaluation(corr)
            err = None
        else:
            ev, err = await _guard(
                "evaluate",
                reasoner.evaluate(state["brief"], findings, corr),
                cfg.reasoner_timeout_s,
                lambda: fallback_evaluation(corr),
            )
        # Çapraz doğrulama kodda da sınanır: hipotezi en az iki specialist desteklemeli
        if ev.cross_validated and len(set(corr.supporting)) < 2:
            ev = ev.model_copy(update={"cross_validated": False})
        return {
            "evaluation": ev.model_dump(),
            "budget": budget.summary(),
            "errors": [err] if err else [],
        }

    async def deep_dive(state: GraphState) -> dict[str, Any]:
        return {
            "deep_dive_round": state.get("deep_dive_round", 0) + 1,
            "deep_dive_tasks": routing.deep_dive_tasks(dict(state), cfg.product_aliases),
        }

    async def synthesize(state: GraphState) -> dict[str, Any]:
        findings = as_findings(state.get("findings"))
        corr = Correlation.model_validate(state["correlation"])
        ev = Evaluation.model_validate(state["evaluation"]) if state.get("evaluation") else None
        rounds = state.get("deep_dive_round", 0)
        if corr.method == "fallback":  # model correlate'te düştü: synthesize de yedekten
            report, err = fallback_report(findings, corr, rounds), None
        else:
            report, err = await _guard(
                "synthesize",
                reasoner.synthesize(state["brief"], findings, corr, ev),
                cfg.reasoner_timeout_s,
                lambda: fallback_report(findings, corr, rounds),
            )
        # Kesinlik kodda: modelin yazdığı değer yok sayılır (akış kontrolü kodda, ADR-001)
        certain = (
            report.source == "model"
            and ev is not None
            and ev.confidence == "high"
            and ev.cross_validated
            and not budget.exhausted
        )
        report = report.model_copy(update={"deep_dive_count": rounds, "certain": certain})
        return {
            "report": report.model_dump(),
            "budget": budget.summary(),
            "errors": [err] if err else [],
        }

    g = StateGraph(GraphState)
    g.add_node("gather", gather)
    g.add_node("run_specialist", run_specialist)  # type: ignore[arg-type]
    g.add_node("correlate", correlate)
    g.add_node("evaluate", evaluate)
    g.add_node("deep_dive", deep_dive)
    g.add_node("synthesize", synthesize)
    g.add_edge(START, "gather")
    g.add_conditional_edges("gather", dispatch, ["run_specialist"])
    g.add_edge("run_specialist", "correlate")
    g.add_edge("correlate", "evaluate")
    g.add_conditional_edges(
        "evaluate",
        routing.route_after_evaluate,
        {"synthesize": "synthesize", "deep_dive": "deep_dive"},
    )
    g.add_conditional_edges("deep_dive", dispatch_deep_dive, ["run_specialist"])
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=checkpointer)
