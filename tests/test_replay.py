"""Kayıt ve replay (ADR-034, demo mode): tool cevapları kayıttan, store bellekte."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sre_agent import demo
from sre_agent.graph.model_specialist import ModelSpecialist
from sre_agent.graph.replay import Recorder, Replay
from sre_agent.llm.budget import Budget
from test_model_specialist import EVIDENCE, FakeModel, FakeServer, call, done
from test_multi_pack import alert, runner


def specialist(model: FakeModel, connect: Any) -> ModelSpecialist:
    return ModelSpecialist(
        model_for=lambda scope: model, connect=connect, servers={"k8s": "k8s"}, budget=Budget()
    )


async def recorded(tmp_path: Path) -> Path:
    server = FakeServer()

    @asynccontextmanager
    async def live(name: str) -> Any:
        yield server

    rec = Recorder(live)
    model = FakeModel([call(1), done()], draft={"summary": "s", "evidence": [0]})
    await specialist(model, rec.connect).run("k8s", "brief", EVIDENCE, None)
    path = tmp_path / "rec.json"
    rec.dump(path, "inc1", [{"id": "r1"}])
    return path


async def test_recorded_answers_are_replayed_without_server(tmp_path: Path) -> None:
    data = Replay.load(await recorded(tmp_path))
    assert data.servers == ["k8s"] and data.data["reports"] == [{"id": "r1"}]
    model = FakeModel([call(1), done()], draft={"summary": "s", "evidence": [0]})
    f = await specialist(model, data.connect).run("k8s", "brief", EVIDENCE, None)
    assert f.tools_used == ["k8s.placement"] and "cus-acme-etcd-0" in f.evidence[0].summary
    assert data.misses == []


async def test_unrecorded_arguments_get_nearest_record_with_note(tmp_path: Path) -> None:
    data = Replay.load(await recorded(tmp_path))
    res = data.answer("k8s", "placement", {"namespace": "loadtest"})
    assert res.content[0].text.startswith("[replay:") and not res.is_error
    assert data.answer("k8s", "events", {}).is_error
    assert len(data.misses) == 2


async def test_demo_without_key_writes_fallback_report(tmp_path: Path, monkeypatch: Any) -> None:
    r = runner()
    r.submit({"alerts": [alert("MilvusWriteDenied", "acme")]})
    await r.wait_idle()
    (rep,) = r.reports.values()
    fixture = tmp_path / "incident.json"
    fixture.write_text(
        json.dumps({"incident_id": "inc1", "reports": [rep.model_dump(mode="json")], "tools": {}})
    )
    config = tmp_path / "config.yaml"
    config.write_text('packs: [{path: packs/vectordb}]\ninvestigation: {mode: "on"}\n')
    monkeypatch.delenv(demo.KEY_ENV, raising=False)
    args = SimpleNamespace(
        fixture=fixture,
        config=config,
        base=Path(__file__).parents[1],
        env=tmp_path / "yok.env",
        out=tmp_path / "out",
    )
    assert await demo.run(args) == 0  # type: ignore[arg-type]
    md = (tmp_path / "out/report.md").read_text()
    assert 0 < md.index("## Soruşturma") < md.index("milvus_querynode_memory_quota")
    assert "Model erişilemedi" in md
