"""Bilgi tabanı (RAG): paket belgeleri Milvus Lite'ta BM25 ile aranır; graph ve product'a
bağlanır."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from sre_agent.adapters.vector_store import KnowledgeBase
from sre_agent.graph.build import GraphConfig, build
from sre_agent.knowledge import Doc, Hit, KnowledgeConfig, brief_section, collect
from sre_agent.llm.budget import Budget
from sre_agent.mcp import product
from test_graph import HIGH, CountingSpecialist, FakeReasoner, ev, report

PACK = Path("packs/vectordb")


@pytest.fixture(scope="module")
def kb(tmp_path_factory: pytest.TempPathFactory) -> KnowledgeBase:
    uri = str(tmp_path_factory.mktemp("kb") / "kb.db")
    base = KnowledgeBase(KnowledgeConfig(uri=uri))
    base.rebuild(collect({"vectordb": PACK}))
    return base


def test_collect_covers_catalog_guides_and_taxonomy() -> None:
    docs = collect({"vectordb": PACK})
    kinds = {d.kind for d in docs}
    assert kinds == {"failure", "guide", "category"}
    etcd = next(d for d in docs if d.id == "vectordb:failure:etcd-erisilemez")
    assert etcd.category and "MilvusEtcdQuorumLost" in etcd.text and "Belirti:" in etcd.text


def test_alert_and_check_names_find_the_failure(kb: KnowledgeBase) -> None:
    hits = kb.search_sync("MilvusEtcdQuorumLost milvus_etcd probe_timeouts", 3, "failure")
    assert hits[0].doc.id == "vectordb:failure:etcd-erisilemez"
    assert all(h.doc.kind == "failure" for h in hits)


def test_symptom_text_finds_the_failure(kb: KnowledgeBase) -> None:
    (top,) = kb.search_sync("deny to write memory quota exhausted", 1)
    assert top.doc.id == "vectordb:failure:yazma-querynode-bellek"


async def test_product_tool_searches_and_validates(kb: KnowledgeBase) -> None:
    srv = product.build({}, {}, {}, kb)
    async with Client(srv) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert tools["knowledge_search"].annotations.read_only_hint  # type: ignore[union-attr]
        r = await c.call_tool("knowledge_search", {"query": "etcd session expired"})
        text = "\n".join(getattr(x, "text", "") for x in r.content)
        assert not r.is_error and "etcd" in text and "Belirti:" in text
        bad = await c.call_tool("knowledge_search", {"query": "x", "kind": "secret"})
        assert bad.is_error


def test_brief_section_lists_failures() -> None:
    d = Doc("p:failure:a", "failure", "etcd'ye erişilemiyor", "database", "x\nBelirti: lease\nK")
    s = brief_section([Hit(d, 9.0)])
    assert "etcd'ye erişilemiyor [database] Belirti: lease" in s and "kanıtla doğrula" in s


class FakeKB:
    def __init__(self, fail: bool = False) -> None:
        self.queries: list[str] = []
        self.fail = fail

    async def __call__(self, query: str) -> list[Any]:
        self.queries.append(query)
        if self.fail:
            raise ConnectionError("milvus kapalı")
        d = Doc("p:failure:etcd", "failure", "etcd'ye erişilemiyor", "database", "Belirti: lease")
        return [Hit(d, 9.0)]


async def _run(kb: FakeKB) -> tuple[dict[str, Any], CountingSpecialist]:
    reps = [report("pilot", ev("milvus_etcd", "metrics", ["database"]))]
    reps[0]["alert"]["name"] = "MilvusEtcdQuorumLost"
    spec = CountingSpecialist()
    briefs: list[str] = []
    orig = spec.run

    async def capture(name: str, brief: str, *a: Any) -> Any:
        briefs.append(brief)
        return await orig(name, brief, *a)

    spec.run = capture  # type: ignore[method-assign, assignment]
    g = build(spec, FakeReasoner([HIGH]), Budget(), GraphConfig(knowledge=kb))
    out = await g.ainvoke({"incident_id": "i", "reports": reps})
    out["_briefs"] = briefs
    return out, spec


async def test_graph_adds_known_failures_to_brief() -> None:
    kb = FakeKB()
    out, _ = await _run(kb)
    assert kb.queries == ["MilvusEtcdQuorumLost milvus_etcd"]  # sorgu kodda kurulur
    assert all("etcd'ye erişilemiyor [database]" in b for b in out["_briefs"])
    assert out["knowledge"][0]["id"] == "p:failure:etcd"


async def test_graph_runs_when_knowledge_is_down() -> None:
    out, _ = await _run(FakeKB(fail=True))
    assert out["report"] and "knowledge: ConnectionError" in out["errors"][0]
    assert not out.get("knowledge")
