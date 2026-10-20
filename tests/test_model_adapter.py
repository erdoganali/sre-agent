"""Model kataloğu ve OpenAI uyumlu adaptör (T2.1): sahte sunucuyla."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from sre_agent.adapters.openai_compat import OpenAICompatModel, split_reasoning
from sre_agent.llm.catalog import Catalog, ModelSpec, load_catalog, resolve_env
from sre_agent.ports.model import Message, ModelError, ModelPort, ToolRequest, ToolSpec

ROOT = Path(__file__).parents[1]
URL = "http://gw.local/llm/v1/chat/completions"


def catalog(**model: Any) -> Catalog:
    spec = {
        "name": "m",
        "url": URL,
        "max_context_tokens": 1000,
        "retries": 1,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "send_reasoning_effort": False,
        "thinking_switch": "chat_template_kwargs.enable_thinking",
        "sampling_params": {"temperature": 1.0},
        "headers": {"Authorization": "Bearer k", "X-Empty": ""},
    } | model
    return Catalog.model_validate(
        {
            "models": {"m": spec},
            "roles": {
                "decision": {
                    "model": "m",
                    "reasoning_effort": "none",
                    "max_tokens": 64,
                    "sampling_params": {"temperature": 0.0},
                },
                "synthesize": {"model": "m", "reasoning_effort": "medium"},
            },
        }
    )


def reply(content: str, **extra: Any) -> dict[str, Any]:
    msg = {"role": "assistant", "content": content} | extra.pop("message", {})
    return {
        "choices": [{"message": msg, "finish_reason": "stop"} | extra],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class Server:
    """Sırayla cevap veren sahte OpenAI uç noktası; gelen istekleri saklar."""

    def __init__(self, *responses: Callable[[dict[str, Any]], httpx.Response] | dict[str, Any]):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        r = self.responses.pop(0)
        return r(json.loads(request.content)) if callable(r) else httpx.Response(200, json=r)

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def model(server: Server, **spec: Any) -> OpenAICompatModel:
    return OpenAICompatModel(
        catalog(**spec), httpx.AsyncClient(transport=httpx.MockTransport(server))
    )


class Verdict(BaseModel):
    category: str
    confidence: float


# ---- katalog


def test_env_override_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "gw")
    assert resolve_env("http://${URL:${HOST:x}:80}/v1") == "http://gw:80/v1"
    monkeypatch.setenv("URL", "http://other")
    assert resolve_env({"u": ["${URL:}"]}) == {"u": ["http://other"]}
    assert resolve_env("${YOK:}") == ""


def test_repo_catalog_loads_and_can_point_to_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QWEN3_6_35B_URL", URL)
    monkeypatch.setenv("SRE_DECISION_MODEL", "gpt-oss-20b")
    c = load_catalog(ROOT / "config/models.yaml")
    assert c.resolve("synthesize")[1].url == URL
    assert c.resolve("decision")[0] == "gpt-oss-20b"


def test_role_must_point_to_known_model() -> None:
    with pytest.raises(ValueError, match="katalogda yok"):
        Catalog.model_validate({"models": {}, "roles": {"decision": {"model": "yok"}}})


def test_only_openai_compatible_provider() -> None:
    with pytest.raises(ValueError, match="provider"):
        ModelSpec(name="x", url=URL, max_context_tokens=1, provider="azure")


@pytest.mark.parametrize(
    ("asked", "supported", "got"),
    [
        ("xhigh", ["low", "medium", "high"], "high"),
        ("max", ["low", "medium", "high"], "high"),
        ("minimal", ["low", "medium", "high"], "low"),
        ("medium", ["low", "high"], "low"),
        ("none", ["low"], "none"),
        ("high", [], None),
    ],
)
def test_reasoning_effort_steps_down(asked: str, supported: list[str], got: str | None) -> None:
    spec = ModelSpec(name="x", url=URL, max_context_tokens=1, supported_reasoning_efforts=supported)
    assert spec.clamp_effort(asked) == got


# ---- istek


async def test_request_shape_role_settings_and_headers() -> None:
    s = Server(reply("tamam"))
    m = model(s)
    assert isinstance(m, ModelPort)
    c = await m.complete("decision", [Message("user", "merhaba")])
    req, body = s.requests[0], s.bodies[0]
    assert str(req.url) == URL  # gateway yolu olduğu gibi kullanılır
    assert req.headers["authorization"] == "Bearer k" and "x-empty" not in req.headers
    assert body["model"] == "m" and body["max_tokens"] == 64
    assert body["temperature"] == 0.0  # rol modeli ezer
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in body
    assert c.text == "tamam" and c.usage.total == 15 and c.model == "m"


async def test_thinking_on_and_effort_sent_when_model_wants_it() -> None:
    s = Server(reply("a"), reply("b"))
    await model(s).complete("synthesize", [Message("user", "x")])
    assert s.bodies[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert "reasoning_effort" not in s.bodies[0]
    m2 = model(s, send_reasoning_effort=True, thinking_switch=None)
    await m2.complete("synthesize", [Message("user", "x")], reasoning_effort="xhigh")
    assert s.bodies[1]["reasoning_effort"] == "high"


@pytest.mark.parametrize(
    ("message", "text", "reasoning"),
    [
        ({"content": "cevap", "reasoning_content": "düşündüm"}, "cevap", "düşündüm"),
        ({"content": "<think>adım</think>\ncevap"}, "cevap", "adım"),
        ({"content": "adım</think>cevap"}, "cevap", "adım"),
        ({"content": "cevap", "reasoning": None}, "cevap", None),
    ],
)
def test_reasoning_is_split_from_answer(
    message: dict[str, Any], text: str, reasoning: str | None
) -> None:
    assert split_reasoning(message) == (text, reasoning)


async def test_retries_on_5xx_then_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None: ...

    monkeypatch.setattr("asyncio.sleep", no_sleep)
    s = Server(lambda _: httpx.Response(503), reply("ok"))
    assert (await model(s).complete("decision", [Message("user", "x")])).text == "ok"
    s2 = Server(lambda _: httpx.Response(503), lambda _: httpx.Response(502))
    with pytest.raises(ModelError, match="başarısız"):
        await model(s2).complete("decision", [Message("user", "x")])
    s3 = Server(lambda _: httpx.Response(401, text="yetkisiz"))
    with pytest.raises(Exception, match="401"):
        await model(s3).complete("decision", [Message("user", "x")])
    assert len(s3.requests) == 1  # 4xx yeniden denenmez


# ---- tipli cevap


async def test_typed_with_structured_output() -> None:
    s = Server(reply('{"category": "network", "confidence": 0.8}'))
    t = await model(s).complete_typed("decision", [Message("user", "x")], Verdict)
    assert t.value == Verdict(category="network", confidence=0.8) and not t.repaired
    assert s.bodies[0]["response_format"]["json_schema"]["name"] == "Verdict"


async def test_typed_falls_back_when_server_rejects_schema_and_repairs_once() -> None:
    s = Server(
        lambda _: httpx.Response(400, text="response_format desteklenmiyor"),
        reply('```json\n{"category": "network"}\n```'),  # eksik alan
        reply('{"category": "network", "confidence": 0.6}'),
    )
    m = model(s)
    t = await m.complete_typed("decision", [Message("user", "x")], Verdict)
    assert t.repaired and t.value.confidence == 0.6
    assert (
        "response_format" not in s.bodies[1]
        and "JSON şemasına" in s.bodies[1]["messages"][0]["content"]
    )
    assert m.capabilities["m"]["structured_output"] is False
    # öğrenilen yetenek sonraki çağrıda kullanılır: doğrudan yedek yol
    s.responses.append(reply('{"category": "x", "confidence": 1}'))
    await m.complete_typed("decision", [Message("user", "y")], Verdict)
    assert "response_format" not in s.bodies[3]


async def test_typed_gives_up_after_one_repair() -> None:
    s = Server(lambda _: httpx.Response(400), reply("değil"), reply("hâlâ değil"))
    with pytest.raises(ModelError, match="şemaya uymadı"):
        await model(s).complete_typed("decision", [Message("user", "x")], Verdict)


# ---- sınıflandırma


def logprob_reply(pairs: list[tuple[str, float]]) -> dict[str, Any]:
    top = [{"token": t, "logprob": lp} for t, lp in pairs]
    return reply(pairs[0][0], logprobs={"content": [{"token": pairs[0][0], "top_logprobs": top}]})


async def test_classify_from_logprobs() -> None:
    s = Server(logprob_reply([("B", math.log(0.6)), ("A", math.log(0.2)), ("x", math.log(0.2))]))
    c = await model(s).classify(
        "decision", [Message("user", "alarm")], ["gürültü", "bilinen", "yeni"]
    )
    label, p = c.top
    assert c.method == "logprobs" and label == "bilinen" and p == pytest.approx(0.75)
    assert sum(c.probs.values()) == pytest.approx(1) and c.probs["yeni"] == 0
    body = s.bodies[0]
    assert body["max_tokens"] == 1 and body["logprobs"] is True
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


async def test_classify_falls_back_to_typed_without_logprobs() -> None:
    s = Server(reply("A"), reply('{"label": "yeni", "confidence": 0.7}'))
    c = await model(s).classify(
        "decision", [Message("user", "alarm")], ["gürültü", "bilinen", "yeni"]
    )
    assert c.method == "typed" and c.top == ("yeni", 0.7)
    assert c.probs["gürültü"] == pytest.approx(0.15) and sum(c.probs.values()) == pytest.approx(1)


# ---- araç döngüsü (T3.4, ADR-031)

TOOLS = [
    ToolSpec(
        "placement",
        "pod yerleşimi",
        {
            "type": "object",
            "properties": {"namespace": {"type": "string"}},
            "required": ["namespace"],
        },
    )
]


def tool_reply(name: str, args: str) -> dict[str, Any]:
    call = {"id": "c1", "type": "function", "function": {"name": name, "arguments": args}}
    return reply("", message={"content": None, "tool_calls": [call]})


async def test_native_tool_call_is_parsed_and_remembered() -> None:
    srv = Server(tool_reply("placement", '{"namespace": "acme"}'), tool_reply("placement", "{}"))
    m = model(srv)
    turn = await m.complete_tools("synthesize", [Message("user", "bak")], TOOLS)
    assert turn.method == "native" and turn.calls == (
        ToolRequest("c1", "placement", {"namespace": "acme"}),
    )
    body = srv.bodies[0]
    assert body["tools"][0]["function"]["name"] == "placement" and body["tool_choice"] == "auto"
    assert m.capabilities["m"]["tools"] is True


async def test_tool_and_assistant_messages_are_sent_in_openai_form() -> None:
    srv = Server(reply("bitti"))
    history = [
        Message("user", "bak"),
        Message("assistant", "", tool_calls=(ToolRequest("c1", "placement", {"namespace": "x"}),)),
        Message("tool", "| node | ... |", tool_call_id="c1"),
    ]
    turn = await model(srv).complete_tools("synthesize", history, TOOLS)
    assert turn.calls == () and turn.text == "bitti"
    sent = srv.bodies[0]["messages"]
    assert sent[1]["tool_calls"][0]["function"] == {
        "name": "placement",
        "arguments": '{"namespace": "x"}',
    }
    assert sent[1]["content"] is None
    assert sent[2] == {"role": "tool", "tool_call_id": "c1", "content": "| node | ... |"}


async def test_server_without_tools_falls_back_to_typed_json() -> None:
    srv = Server(
        lambda b: httpx.Response(400, json={"error": "tools not supported"}),
        reply(
            json.dumps({"action": "call", "tool": "placement", "arguments": {"namespace": "acme"}})
        ),
        reply('{"action": "done", "note": "etcd kaybı"}'),
    )
    m = model(srv)
    first = await m.complete_tools("synthesize", [Message("user", "bak")], TOOLS)
    assert first.method == "json" and first.calls[0].name == "placement"
    assert m.capabilities["m"]["tools"] is False
    second = await m.complete_tools("synthesize", [Message("user", "bak")], TOOLS)
    assert second.calls == () and second.text == "etcd kaybı"
    assert "tools" not in srv.bodies[2]  # öğrenildi: native yeniden denenmez


async def test_unknown_native_tool_falls_back_to_json() -> None:
    srv = Server(
        tool_reply("rm_rf", "{}"),
        reply('{"action": "done", "note": "yok"}'),
    )
    turn = await model(srv).complete_tools("synthesize", [Message("user", "bak")], TOOLS)
    assert turn.method == "json" and turn.calls == ()


async def test_json_fallback_rejects_unknown_tool() -> None:
    srv = Server(
        lambda b: httpx.Response(404, json={}),
        reply('{"action": "call", "tool": "delete_pod", "arguments": {}}'),
    )
    with pytest.raises(ModelError, match="bilinmeyen araç"):
        await model(srv).complete_tools("synthesize", [Message("user", "bak")], TOOLS)


async def test_cached_prompt_tokens_are_read() -> None:
    body = reply("ok") | {
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 1024},
        }
    }
    c = await model(Server(body)).complete("decision", [Message("user", "x")])
    assert c.usage.cached_tokens == 1024 and c.usage.total == 1205


async def test_rate_limit_waits_for_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    waits: list[float] = []

    async def fake_sleep(s: float) -> None:
        waits.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    srv = Server(lambda b: httpx.Response(429, headers={"retry-after": "17"}, json={}), reply("ok"))
    c = await model(srv).complete("decision", [Message("user", "x")])
    assert c.text == "ok" and waits == [17.0]


async def test_max_concurrency_limits_parallel_requests() -> None:
    import asyncio

    active = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=reply("ok"))

    m = OpenAICompatModel(
        catalog(max_concurrency=2), httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    await asyncio.gather(*(m.complete("decision", [Message("user", "x")]) for _ in range(6)))
    assert peak == 2
