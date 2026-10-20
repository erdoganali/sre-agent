"""Investigation graph iskeleti (T3.2): routing, uçtan uca akış, yedekler, checkpoint."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sre_agent.graph import routing
from sre_agent.graph.build import GraphConfig, build, gather_state
from sre_agent.graph.schemas import Correlation, DeepDive, Evaluation, FinalReport, Finding
from sre_agent.graph.specialists import EvidenceSpecialist
from sre_agent.llm.budget import Budget, Limits


def ev(check: str, cap: str, cats: list[str]) -> dict[str, Any]:
    return {
        "check": check,
        "capability": cap,
        "categories": cats,
        "status": "anomaly",
        "summary": f"{check} anomali",
    }


def report(ns: str, *evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "alert": {"name": "PodRestarting", "labels": {"namespace": ns}},
        "anomalies_by_category": {},
        "evidence": list(evidence),
    }


NODE_LOSS = [
    report(
        "acme",
        ev("node_events", "k8s", ["hardware_problem"]),
        ev("pod_restarts", "k8s", ["capacity_performance"]),
        ev("golden_signals_baseline", "metrics", ["capacity_performance"]),
    ),
    report(
        "loadtest",
        ev("node_recreated", "metrics", ["hardware_problem"]),
        ev("milvus_health", "metrics", ["database"]),
    ),
]


class FakeReasoner:
    """Senaryoya göre cevap verir; çağrıları sayar."""

    def __init__(self, evaluations: list[Evaluation], fail: set[str] | None = None):
        self.evaluations = list(evaluations)
        self.fail = fail or set()
        self.calls: list[str] = []

    async def correlate(self, brief: str, findings: dict[str, Finding]) -> Correlation:
        self.calls.append("correlate")
        if "correlate" in self.fail:
            raise RuntimeError("model düştü")
        return Correlation(
            category="hardware_problem", root_cause="node kaybı", supporting=sorted(findings)
        )

    async def evaluate(
        self, brief: str, findings: dict[str, Finding], c: Correlation
    ) -> Evaluation:
        self.calls.append("evaluate")
        return self.evaluations.pop(0)

    async def synthesize(
        self, brief: str, findings: dict[str, Finding], c: Correlation, e: Evaluation | None
    ) -> FinalReport:
        self.calls.append("synthesize")
        if "synthesize" in self.fail:
            raise RuntimeError("model düştü")
        return FinalReport(summary="node kaybı", category=c.category, root_cause=c.root_cause)


class CountingSpecialist(EvidenceSpecialist):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def run(
        self, name: str, brief: str, evidence: list[dict[str, Any]], question: str | None
    ) -> Finding:
        self.calls.append((name, question))
        return await super().run(name, brief, evidence, question)


async def run(
    reasoner: FakeReasoner,
    spec: CountingSpecialist | None = None,
    budget: Budget | None = None,
    reports: list[dict[str, Any]] | None = None,
    saver: Any = None,
    thread: str = "t1",
) -> dict[str, Any]:
    g = build(
        spec or CountingSpecialist(),
        reasoner,
        budget or Budget(),
        GraphConfig(product_checks=frozenset({"milvus_health"})),
        saver or InMemorySaver(),
    )
    out: dict[str, Any] = await g.ainvoke(
        {"incident_id": "inc1", "reports": reports or NODE_LOSS},
        {"configurable": {"thread_id": thread}},
    )
    return out


HIGH = Evaluation(confidence="high", cross_validated=True)


# ---- routing (kod)


@pytest.mark.parametrize(
    ("state", "route"),
    [
        ({"evaluation": HIGH.model_dump()}, "synthesize"),
        (
            {
                "evaluation": {
                    "confidence": "high",
                    "cross_validated": False,
                    "suggested_deep_dives": [{"specialist": "k8s", "question": "?"}],
                }
            },
            "deep_dive",
        ),
        (
            {
                "evaluation": {
                    "confidence": "low",
                    "cross_validated": False,
                    "suggested_deep_dives": [{"specialist": "k8s", "question": "?"}],
                },
                "deep_dive_round": 2,
            },
            "synthesize",
        ),
        (
            {
                "evaluation": {
                    "confidence": "low",
                    "cross_validated": False,
                    "suggested_deep_dives": [{"specialist": "uydurma", "question": "?"}],
                }
            },
            "synthesize",
        ),
        (
            {
                "evaluation": {
                    "confidence": "low",
                    "suggested_deep_dives": [{"specialist": "k8s", "question": "?"}],
                },
                "budget": {"wrap_up": True},
            },
            "synthesize",
        ),
    ],
)
def test_route_after_evaluate(state: dict[str, Any], route: str) -> None:
    assert routing.route_after_evaluate(state) == route


@pytest.mark.parametrize(
    ("name", "canon"),
    [
        ("k8s specialist", "k8s"),
        ("Kubernetes", "k8s"),
        ("log specialist", "logs"),
        ("metric", "metrics"),
        ("Milvus", "product"),
        ("tracess", "traces"),
        ("hava durumu", None),
    ],
)
def test_specialist_names_normalized(name: str, canon: str | None) -> None:
    assert routing.normalize_specialist(name, frozenset({"milvus"})) == canon
    if name == "Milvus":
        assert routing.normalize_specialist(name) is None  # ürün adı çekirdekte değil, paketten


def test_gather_maps_evidence_and_forces_k8s_on_node_loss() -> None:
    st = gather_state("inc1", NODE_LOSS, frozenset({"milvus_health"}))
    assert set(st["specialists"]) == {"k8s", "metrics", "product"}
    assert [e["check"] for e in st["evidence"]["product"]] == ["milvus_health"]
    assert "Incident inc1: 2 rapor" in st["brief"]
    only_metrics = [report("x", ev("golden_signals_baseline", "metrics", ["capacity_performance"]))]
    assert gather_state("i", only_metrics, frozenset())["specialists"] == ["metrics"]
    # paket alarmı: product da açılır (ikinci görüş; tek specialist'te çapraz doğrulama yok)
    pack_alert = [{**only_metrics[0], "packs": ["vectordb"]}]
    assert gather_state("i", pack_alert, frozenset())["specialists"] == ["metrics", "product"]
    ok_check = [
        report("x", ev("golden_signals_baseline", "metrics", []), ev("milvus_etcd", "x", []))
    ]
    ok_check[0]["evidence"][1]["status"] = "ok"
    assert "product" in gather_state("i", ok_check, frozenset({"milvus_etcd"}))["specialists"]


def test_single_specialist_evaluation_says_why() -> None:
    from sre_agent.graph.reasoner import fallback_evaluation, passthrough
    from sre_agent.graph.schemas import Finding

    ev1 = fallback_evaluation(passthrough({"metrics": Finding(specialist="metrics", summary="s")}))
    assert ev1.note == "tek specialist; çapraz doğrulama yapılamadı" and not ev1.cross_validated


# ---- uçtan uca


async def test_cross_validated_goes_straight_to_synthesis() -> None:
    r = FakeReasoner([HIGH])
    spec = CountingSpecialist()
    out = await run(r, spec)
    assert r.calls == ["correlate", "evaluate", "synthesize"]
    assert {n for n, _ in spec.calls} == {"k8s", "metrics", "product"}  # paralel, birer kez
    assert out["report"]["source"] == "model" and out["report"]["deep_dive_count"] == 0


async def test_deep_dive_targets_named_specialists_and_stops_after_two_rounds() -> None:
    weak = Evaluation(
        confidence="medium",
        cross_validated=False,
        suggested_deep_dives=[DeepDive(specialist="kubernetes", question="etcd?")],
    )
    r = FakeReasoner([weak, weak, weak])
    spec = CountingSpecialist()
    out = await run(r, spec)
    assert r.calls.count("evaluate") == 3 and r.calls[-1] == "synthesize"
    assert [c for c in spec.calls if c[1]] == [("k8s", "etcd?"), ("k8s", "etcd?")]
    assert out["report"]["deep_dive_count"] == 2
    assert any("deep dive sorusu" in o for o in out["findings"]["k8s"]["observations"])


async def test_model_failure_falls_back_and_always_reports() -> None:
    r = FakeReasoner([HIGH], fail={"correlate"})
    out = await run(r)
    assert r.calls == ["correlate"]  # yedek yol: evaluate/synthesize modeli çağrılmaz
    rep = out["report"]
    assert rep["source"] == "fallback" and rep["certain"] is False
    assert out["errors"] and "correlate" in out["errors"][0]


async def test_single_specialist_passthrough_skips_model() -> None:
    r = FakeReasoner([HIGH])
    one = [report("x", ev("golden_signals_baseline", "metrics", ["capacity_performance"]))]
    out = await run(r, reports=one)
    assert "correlate" not in r.calls and out["correlation"]["method"] == "passthrough"
    assert (
        r.calls == ["synthesize"] and out["report"]["source"] == "model"
    )  # yalnız correlate atlanır


async def test_budget_wrap_up_forces_early_synthesis() -> None:
    weak = Evaluation(
        confidence="low",
        cross_validated=False,
        suggested_deep_dives=[DeepDive(specialist="k8s", question="?")],
    )
    b = Budget(limits=Limits(run_soft=0))  # yumuşak sınır baştan dolu
    r = FakeReasoner([weak])
    out = await run(r, budget=b)
    assert out["report"]["deep_dive_count"] == 0 and r.calls[-1] == "synthesize"


async def test_resume_from_checkpoint_does_not_rerun_specialists() -> None:
    saver = InMemorySaver()
    spec = CountingSpecialist()
    r = FakeReasoner([HIGH], fail={"synthesize"})
    first = await run(r, spec, saver=saver, thread="t-resume")
    assert first["report"]["source"] == "fallback"  # synthesize düştü ama rapor çıktı
    g = build(spec, FakeReasoner([]), Budget(), GraphConfig(), saver)
    snap = await g.aget_state({"configurable": {"thread_id": "t-resume"}})
    assert snap.values["report"] and not snap.next  # koşu tamamlanmış, state checkpoint'te
    assert len(spec.calls) == 3


def test_fallback_needs_two_specialists_otherwise_unknown() -> None:
    from sre_agent.graph.reasoner import fallback_correlation

    def f(name: str, *suspects: str) -> Finding:
        return Finding(specialist=name, summary=name, suspects=list(suspects))

    split = fallback_correlation({"a": f("a", "capacity_performance"), "b": f("b", "database")})
    assert split.category == "unknown" and "capacity_performance, database" in split.root_cause
    assert {r.category for r in split.rejected} == {"capacity_performance", "database"}
    agree = fallback_correlation(
        {"a": f("a", "hardware_problem"), "b": f("b", "hardware_problem"), "c": f("c", "database")}
    )
    assert agree.category == "hardware_problem" and agree.supporting == ["a", "b"]
    assert [(r.category, r.reason) for r in agree.rejected] == [
        ("database", "yalnız c gösteriyor (tek kaynak)")
    ]


# ---- model çıktısının kodda sınanması: kategori, kesinlik, özet


class SureReasoner(FakeReasoner):
    """Model her raporu 'kesin' sayar ve destekçi uydurur; kod ikisini de yok saymalı."""

    async def correlate(self, brief: str, findings: dict[str, Finding]) -> Correlation:
        c = await super().correlate(brief, findings)
        return c.model_copy(update={"supporting": ["metrics", "k8s", "uydurma"]})

    async def synthesize(
        self, brief: str, findings: dict[str, Finding], c: Correlation, e: Evaluation | None
    ) -> FinalReport:
        rep = await super().synthesize(brief, findings, c, e)
        return rep.model_copy(update={"certain": True})


AGREE = [  # iki specialist'in de en güçlü adayı hardware_problem
    report(
        "acme",
        ev("node_events", "k8s", ["hardware_problem"]),
        ev("node_recreated", "metrics", ["hardware_problem"]),
        ev("pod_restarts", "k8s", ["capacity_performance"]),
    )
]


async def test_certain_is_decided_in_code_not_by_model() -> None:
    assert (await run(SureReasoner([HIGH]), reports=AGREE))["report"]["certain"] is True
    weak = Evaluation(confidence="medium", cross_validated=True)
    assert (await run(SureReasoner([weak]), reports=AGREE))["report"]["certain"] is False
    exhausted = Budget(limits=Limits(run_hard=0))
    out = await run(SureReasoner([HIGH]), budget=exhausted, reports=AGREE)
    assert out["report"]["certain"] is False


async def test_supporting_counts_only_each_specialists_top_suspect() -> None:
    out = await run(SureReasoner([HIGH]), reports=AGREE)
    assert out["correlation"]["supporting"] == ["k8s", "metrics"]  # model'in "uydurma"sı yok
    lower = [
        report(
            "x",
            ev("node_events", "k8s", ["hardware_problem"]),
            ev("golden_signals_baseline", "metrics", ["capacity_performance"]),
            ev("node_recreated", "metrics", ["hardware_problem"]),
        )
    ]
    out = await run(SureReasoner([HIGH]), reports=lower)  # metrics'te hardware ikinci sırada
    assert out["correlation"]["supporting"] == ["k8s"]
    assert out["evaluation"]["cross_validated"] is False and out["report"]["certain"] is False


class QueueModel:
    """complete_typed çağrılarına sırayla hazır cevap verir."""

    def __init__(self, *values: Any):
        from sre_agent.ports.model import Completion, Usage

        self.values = list(values)
        self.systems: list[str] = []
        self._c = Completion(text="", model="m", usage=Usage(), latency_s=0.0)

    async def complete_typed(self, role: str, messages: Any, schema: Any, **_: Any) -> Any:
        from sre_agent.ports.model import Typed

        self.systems.append(messages[0].content)
        return Typed(value=schema.model_validate(self.values.pop(0)), completion=self._c)


FINDINGS = {"k8s": Finding(specialist="k8s", summary="node kayboldu")}


async def test_invalid_category_is_retried_once_then_unknown() -> None:
    from sre_agent.graph.reasoner import ModelReasoner

    m = QueueModel(
        {"category": "root_cause", "root_cause": "x"},
        {"category": "Hardware Problem", "root_cause": "node kaybı"},
    )
    c = await ModelReasoner(m).correlate("brief", FINDINGS)  # type: ignore[arg-type]
    assert c.category == "hardware_problem" and len(m.systems) == 2
    assert "root_cause" in m.systems[1] and "Specialist bulgularını" in m.systems[1]

    m = QueueModel(
        {"category": "infra", "root_cause": "x"}, {"category": "yine", "root_cause": "x"}
    )
    c = await ModelReasoner(m).correlate("brief", FINDINGS)  # type: ignore[arg-type]
    assert c.category == "unknown"


async def test_rejected_with_invalid_category_is_dropped() -> None:
    from sre_agent.graph.reasoner import ModelReasoner

    m = QueueModel(
        {
            "category": "hardware_problem",
            "root_cause": "x",
            "rejected": [
                {"category": "Capacity_Performance", "reason": "bellek normal"},
                {"category": "uydurma", "reason": "?"},
            ],
        }
    )
    c = await ModelReasoner(m).correlate("brief", FINDINGS)  # type: ignore[arg-type]
    assert [(r.category, r.reason) for r in c.rejected] == [
        ("capacity_performance", "bellek normal")
    ]


async def test_synthesize_clips_summary_and_keeps_valid_category() -> None:
    from sre_agent.graph.reasoner import SUMMARY_MAX, ModelReasoner

    corr = Correlation(category="hardware_problem", root_cause="node kaybı")
    m = QueueModel(
        {"summary": "a" * 5000, "category": "Infrastructure/Hardware", "root_cause": "x"}
    )
    rep = await ModelReasoner(m).synthesize("brief", FINDINGS, corr, HIGH)  # type: ignore[arg-type]
    assert len(rep.summary) == SUMMARY_MAX and rep.summary.endswith("…")
    assert rep.category == "hardware_problem"  # geçersiz → hipotezin kategorisi
    assert "Türkçe yaz" in m.systems[0] and "yazma işlemi" in m.systems[0]


def test_brief_carries_incident_time_and_node_loss_opens_product() -> None:
    reps = [
        dict(r, alert=dict(r["alert"], starts_at=t))
        for r, t in zip(NODE_LOSS, ["2026-10-09T14:38:00Z", "2026-10-09T14:34:18Z"], strict=True)
    ]
    st = gather_state("i", reps, frozenset({"milvus_health"}))
    assert "ilk alarm 2026-10-09T14:34:18Z, son alarm 2026-10-09T14:38:00Z" in st["brief"]
    assert {"k8s", "product"} <= set(st["specialists"])


async def test_investigation_is_one_trace_with_a_span_per_step(monkeypatch: Any) -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from sre_agent.graph import build as build_mod
    from sre_agent.graph import investigator as inv_mod
    from sre_agent.graph.replay import MemoryStore

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    for mod in (build_mod, inv_mod):
        monkeypatch.setattr(mod, "tracer", provider.get_tracer("test"))
    inv = inv_mod.Investigator(
        inv_mod.InvestigationConfig(mode=inv_mod.GraphMode.ON),
        MemoryStore({"inc1": NODE_LOSS}),  # type: ignore[arg-type]
        lambda _b: FakeReasoner([HIGH]),
        specialist=CountingSpecialist(),
        graph_cfg=GraphConfig(product_checks=frozenset({"milvus_health"})),
    )
    await inv.run("inc1")
    spans = {s.name: s for s in exporter.get_finished_spans()}
    root = spans["investigation inc1"]
    assert root.parent is None and (root.attributes or {})["sre_agent.investigation.category"]
    steps = {"specialist k8s", "specialist metrics", "specialist product", "correlate"}
    for name in steps | {"evaluate", "synthesize"}:
        assert spans[name].parent.span_id == root.context.span_id  # type: ignore[union-attr]
        assert spans[name].context.trace_id == root.context.trace_id
