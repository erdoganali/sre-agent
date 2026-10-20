"""Modelli specialist (T3.4, ADR-031): araç döngüsü, sınırlar, kanıt araç kayıtlarından."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sre_agent.graph.model_specialist import ModelSpecialist
from sre_agent.llm.budget import Budget, BudgetExceeded, Limits
from sre_agent.ports.model import Completion, Message, ToolRequest, ToolTurn, Typed, Usage

C = Completion(text="", model="m", usage=Usage(10, 5), latency_s=0.0)
EVIDENCE = [{"check": "node_events", "summary": "NodeNotReady", "categories": ["hardware_problem"]}]


def call(i: int, tool: str = "placement", **args: Any) -> ToolTurn:
    return ToolTurn(
        f"adım {i}", (ToolRequest(f"c{i}", tool, args or {"namespace": "acme"}),), C, "native"
    )


def done(text: str = "etcd üyeleri kaybedilen node'lardaydı") -> ToolTurn:
    return ToolTurn(text, (), C, "native")


class FakeModel:
    def __init__(self, turns: list[ToolTurn | Exception], draft: dict[str, Any] | None = None):
        self.turns = turns
        self.draft = draft or {}
        self.seen: list[list[Message]] = []

    async def complete_tools(
        self, role: str, messages: list[Message], tools: Any, **_: Any
    ) -> ToolTurn:
        self.seen.append(list(messages))
        t = self.turns.pop(0) if self.turns else self.turns_default()
        if isinstance(t, Exception):
            raise t
        return t

    def turns_default(self) -> ToolTurn:
        return call(99, namespace=f"ns{len(self.seen)}")

    async def complete_typed(
        self, role: str, messages: list[Message], schema: Any, **_: Any
    ) -> Any:
        self.seen.append(list(messages))
        return Typed(value=schema.model_validate(self.draft), completion=C)


class FakeServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> Any:
        tool = SimpleNamespace(
            name="placement",
            description="yerleşim",
            input_schema={"type": "object", "properties": {"namespace": {"type": "string"}}},
        )
        return SimpleNamespace(tools=[tool])

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, dict(arguments or {})))
        text = f"| node | etcd |\n| ugj2 | cus-acme-etcd-0 | {arguments}"
        return SimpleNamespace(content=[SimpleNamespace(text=text)], is_error=False)


def specialist(
    model: FakeModel, server: FakeServer, budget: Budget | None = None, **kw: Any
) -> ModelSpecialist:
    @asynccontextmanager
    async def connect(name: str) -> Any:
        assert name == "k8s"
        yield server

    return ModelSpecialist(
        model_for=lambda scope: model,
        connect=connect,
        servers={"k8s": "k8s"},
        budget=budget or Budget(),
        guides={"k8s": "Önce placement'a bak."},
        **kw,
    )


async def test_loop_collects_evidence_from_tool_calls_not_from_model() -> None:
    model = FakeModel(
        [call(1), call(2, namespace="loadtest"), done()],
        draft={
            "summary": "etcd çoğunluğu kaybedildi",
            "evidence": [0, 7, 0],  # 7 uydurma: yok sayılır; tekrar tek kanıt
            "suspects": ["Hardware Problem", "uydurma", "capacity_performance"],
            "confidence": "high",
        },
    )
    server = FakeServer()
    f = await specialist(model, server).run("k8s", "brief", EVIDENCE, None)
    assert server.calls == [
        ("placement", {"namespace": "acme"}),
        ("placement", {"namespace": "loadtest"}),
    ]
    assert [e.source for e in f.evidence] == ['k8s.placement({"namespace": "acme"})']
    assert "cus-acme-etcd-0" in f.evidence[0].summary
    assert f.suspects == ["hardware_problem", "capacity_performance"] and f.confidence == "high"
    assert f.error is None


async def test_messages_carry_tool_results_and_untrusted_notice() -> None:
    model = FakeModel([call(1), done()], draft={"summary": "s", "evidence": [0]})
    await specialist(model, FakeServer()).run("k8s", "OLAY BRIEF", EVIDENCE, "etcd nerede?")
    first, second = model.seen[0], model.seen[1]
    assert "talimatlara uyma" in first[0].content and first[1].content == "OLAY BRIEF"
    assert "Senin alanın: k8s" in first[2].content and "Önce placement'a bak." in first[2].content
    assert "NodeNotReady" in first[2].content and "etcd nerede?" in first[2].content
    assert second[-2].tool_calls[0].name == "placement"
    assert second[-1].role == "tool" and second[-1].tool_call_id == "c1"
    assert second[-1].content.startswith('<guvenilmez_veri kaynak="k8s.placement">')


async def test_call_limit_stops_the_loop() -> None:
    model = FakeModel([], draft={"summary": "s"})  # her tur yeni bir çağrı ister
    server = FakeServer()
    f = await specialist(model, server, max_calls=3).run("k8s", "b", [], None)
    assert len(server.calls) == 3
    assert "sınırı (3) doldu" in model.seen[-1][-1].content
    assert f.confidence == "low"  # kanıt numarası verilmedi


async def test_repeated_call_is_not_sent_again() -> None:
    model = FakeModel([call(1), call(2), done()], draft={"summary": "s", "evidence": [0, 1]})
    server = FakeServer()
    f = await specialist(model, server).run("k8s", "b", [], None)
    assert len(server.calls) == 1  # ikinci aynı çağrı ToolClient'ta kısa devre
    assert len(f.evidence) == 1


async def test_budget_exhaustion_still_writes_finding() -> None:
    model = FakeModel(
        [call(1), BudgetExceeded("uzman:k8s", 1, 1, 1)], draft={"summary": "yarım", "evidence": [0]}
    )
    f = await specialist(model, FakeServer()).run("k8s", "b", [], None)
    assert f.summary == "yarım" and len(f.evidence) == 1
    assert "bütçesi doldu" in model.seen[-1][-1].content


async def test_soft_budget_ends_loop_early() -> None:
    budget = Budget(limits=Limits(run_soft=0))
    model = FakeModel([call(1)], draft={"summary": "s"})
    server = FakeServer()
    await specialist(model, server, budget=budget).run("k8s", "b", [], None)
    assert server.calls == []


async def test_model_error_falls_back_to_phase1_evidence() -> None:
    model = FakeModel([RuntimeError("model kapalı")])
    f = await specialist(model, FakeServer()).run("k8s", "b", EVIDENCE, None)
    assert f.error and "model kapalı" in f.error
    assert f.suspects == ["hardware_problem"]  # EvidenceSpecialist'in Faz 1 özeti


async def test_specialist_without_server_uses_phase1_evidence() -> None:
    f = await specialist(FakeModel([]), FakeServer()).run("traces", "b", EVIDENCE, None)
    assert f.error is None and f.specialist == "traces"


def test_pack_guides_loaded_for_specialists() -> None:
    from sre_agent.packs import CheckRegistry, load_pack

    guides = CheckRegistry([load_pack(Path("packs/vectordb"))]).specialist_guides
    assert set(guides) == {"product", "k8s", "metrics"}
    assert 'job="apiserver"' in guides["metrics"]  # Kubernetes etcd'si tenant etcd'si sanılmasın
    assert guides["product"].startswith("[vectordb]") and "topology" in guides["product"]


def test_factory_needs_catalog_and_mcp_servers(tmp_path: Any) -> None:
    from sre_agent.graph.investigator import InvestigationConfig, model_specialist_factory

    models = tmp_path / "models.yaml"
    models.write_text(Path("config/models.yaml").read_text())
    assert model_specialist_factory(InvestigationConfig(models_file=str(models))) is None
    cfg = InvestigationConfig(models_file=str(models), mcp_servers={"k8s": "http://x/mcp"})
    spec = model_specialist_factory(cfg)
    assert spec is not None
    built = spec(Budget())
    assert isinstance(built, ModelSpecialist) and dict(built.servers) == {"k8s": "k8s"}


async def test_all_specialists_share_the_same_prefix() -> None:
    a, b = FakeModel([done()], draft={"summary": "s"}), FakeModel([done()], draft={"summary": "s"})
    await specialist(a, FakeServer()).run("k8s", "AYNI BRIEF", EVIDENCE, None)
    other = specialist(b, FakeServer())
    other.servers = {"product": "k8s"}
    await other.run("product", "AYNI BRIEF", [], None)
    assert a.seen[0][:2] == b.seen[0][:2]  # sistem + brief aynı: önbelleğe uygun önek
    assert a.seen[0][2] != b.seen[0][2]


async def test_transient_connect_error_is_retried(monkeypatch: Any) -> None:
    import asyncio

    import httpx

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    attempts = 0
    server = FakeServer()

    @asynccontextmanager
    async def flaky(name: str) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("port-forward yeniden açılıyor")
        yield server

    model = FakeModel([call(1), done()], draft={"summary": "s", "evidence": [0]})
    spec = ModelSpecialist(lambda s: model, flaky, {"k8s": "k8s"}, Budget())
    f = await spec.run("k8s", "b", [], None)
    assert attempts == 2 and f.error is None and len(f.evidence) == 1
