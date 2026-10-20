"""OpenAI uyumlu model adaptörü (vLLM ya da aynı API'yi sunan bir gateway).

Sağlayıcıya özel hiçbir şey kodda değil, katalogdadır (ADR-027). Sunucunun yetenekleri
(structured output, logprob) ilk kullanımda yoklanır; yoksa yedek yol kullanılır.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import string
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from sre_agent.llm.catalog import Catalog, ModelSpec, RoleSpec
from sre_agent.ports.model import (
    Classification,
    Completion,
    Message,
    ModelError,
    T,
    ToolRequest,
    ToolSpec,
    ToolTurn,
    Typed,
    Usage,
)

_THINK = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}


def _set_path(body: dict[str, Any], dotted: str, value: Any) -> None:
    *parents, leaf = dotted.split(".")
    node = body
    for p in parents:
        node = node.setdefault(p, {})
    node[leaf] = value


def _wire(m: Message) -> dict[str, Any]:
    """Mesajın OpenAI biçimi: araç çağrısı isteyen assistant ve araç sonucu (tool) dahil."""
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["content"] = m.content or None
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in m.tool_calls
        ]
    return out


def _as_text(m: Message) -> Message:
    """Tipli JSON yedeği için: araç mesajları düz metne çevrilir (araç desteklemeyen sunucu)."""
    if m.role == "tool":
        return Message("user", f"ARAÇ SONUCU ({m.tool_call_id}):\n{m.content}")
    if m.tool_calls:
        calls = "; ".join(
            f"{c.name}({json.dumps(c.arguments, ensure_ascii=False)})" for c in m.tool_calls
        )
        return Message("assistant", f"{m.content}\nÇAĞRI: {calls}".strip())
    return m


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def split_reasoning(message: dict[str, Any]) -> tuple[str, str | None]:
    """Cevabı düşünme metninden ayırır (reasoning_content / reasoning alanı ya da <think>)."""
    text = message.get("content") or ""
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    found = _THINK.findall(text)
    if found:
        reasoning = "\n".join(filter(None, [reasoning, *found]))
        text = _THINK.sub("", text)
    if "</think>" in text:  # açılış etiketi şablonda kalmış olabilir
        head, _, text = text.partition("</think>")
        reasoning = "\n".join(filter(None, [reasoning, head]))
    return text.strip(), (reasoning or None)


class OpenAICompatModel:
    def __init__(self, catalog: Catalog, client: httpx.AsyncClient | None = None):
        self.catalog = catalog
        self._client = client or httpx.AsyncClient()
        # model anahtarı → yetenek → destekleniyor mu (ilk denemede öğrenilir)
        self.capabilities: dict[str, dict[str, bool]] = {}
        # model anahtarı → eşzamanlılık sınırı (katalogda max_concurrency)
        self._gates: dict[str, asyncio.Semaphore] = {
            k: asyncio.Semaphore(m.max_concurrency)
            for k, m in catalog.models.items()
            if m.max_concurrency
        }

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- istek kurma

    def _body(
        self,
        spec: ModelSpec,
        role: RoleSpec,
        messages: list[Message],
        max_tokens: int | None,
        effort: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": spec.name,
            "messages": [_wire(m) for m in messages],
        }
        body.update(spec.sampling_params)
        body.update(role.sampling_params)
        body = _merge(body, spec.extra_body)
        tokens = max_tokens or role.max_tokens
        if tokens:
            body["max_tokens"] = tokens
        effort = spec.clamp_effort(effort if effort is not None else role.reasoning_effort)
        if effort == "none":
            body.pop("reasoning_effort", None)  # sampling_params'tan gelmiş olabilir
        if spec.thinking_switch and effort is not None:
            _set_path(body, spec.thinking_switch, effort != "none")
        if effort not in (None, "none") and spec.send_reasoning_effort:
            body["reasoning_effort"] = effort
        return body

    async def _post(self, spec: ModelSpec, body: dict[str, Any]) -> dict[str, Any]:
        headers = {k: v for k, v in spec.headers.items() if v}
        last: Exception | None = None
        for attempt in range(spec.retries + 1):
            try:
                resp = await self._client.post(
                    spec.url,
                    json=body,
                    headers=headers,
                    timeout=httpx.Timeout(spec.timeout_s, connect=spec.connect_timeout_s),
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise ModelError(
                    f"model adresine bağlanılamadı ({spec.url}): {type(exc).__name__} {exc}"
                ) from exc
            except httpx.TransportError as exc:
                last = exc
            else:
                if resp.status_code < 400:
                    data: dict[str, Any] = resp.json()
                    return data
                if resp.status_code not in _RETRY_STATUS:
                    raise _HttpError(resp.status_code, resp.text[:500])
                last = _HttpError(resp.status_code, resp.text[:500])
                wait = _retry_after(resp)
                if wait is not None and attempt < spec.retries:
                    # Kota (429): sunucunun söylediği kadar beklenir (en çok 60 sn)
                    await asyncio.sleep(min(wait, 60))
                    continue
            if attempt < spec.retries:
                await asyncio.sleep(min(2**attempt, 8))
        raise ModelError(f"model çağrısı başarısız ({spec.name}): {last}")

    async def _call(
        self,
        role_name: str,
        messages: list[Message],
        max_tokens: int | None,
        effort: str | None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[Completion, dict[str, Any]]:
        key, spec, role = self.catalog.resolve(role_name)
        body = self._body(spec, role, messages, max_tokens, effort)
        if extra:
            body.update(extra)
        gate = self._gates.get(key)
        if gate is not None:
            async with gate:
                t0 = time.monotonic()
                data = await self._post(spec, body)
        else:
            t0 = time.monotonic()
            data = await self._post(spec, body)
        latency = time.monotonic() - t0
        try:
            choice = data["choices"][0]
        except (KeyError, IndexError) as exc:
            raise ModelError(f"beklenmeyen cevap biçimi ({key}): {str(data)[:300]}") from exc
        text, reasoning = split_reasoning(choice.get("message") or {})
        u = data.get("usage") or {}
        completion = Completion(
            text=text,
            model=key,
            usage=Usage(
                int(u.get("prompt_tokens") or 0),
                int(u.get("completion_tokens") or 0),
                int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
            ),
            latency_s=latency,
            reasoning=reasoning,
            finish_reason=choice.get("finish_reason"),
        )
        return completion, choice

    def _cap(self, role_name: str, name: str) -> bool | None:
        key = self.catalog.roles[role_name].model
        return self.capabilities.get(key, {}).get(name)

    def _learn(self, role_name: str, name: str, ok: bool) -> None:
        key = self.catalog.roles[role_name].model
        self.capabilities.setdefault(key, {})[name] = ok

    # ---- port

    async def complete(
        self,
        role: str,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Completion:
        completion, _ = await self._call(role, messages, max_tokens, reasoning_effort)
        return completion

    async def complete_typed(
        self,
        role: str,
        messages: list[Message],
        schema: type[T],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Typed[T]:
        json_schema = schema.model_json_schema()
        if self._cap(role, "structured_output") is not False:
            fmt = {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": json_schema},
                }
            }
            try:
                completion, _ = await self._call(
                    role, messages, max_tokens, reasoning_effort, extra=fmt
                )
                self._learn(role, "structured_output", True)
                return Typed(value=_parse(schema, completion.text), completion=completion)
            except _HttpError as exc:
                if exc.status != 400:
                    raise ModelError(str(exc)) from exc
                self._learn(role, "structured_output", False)
            except _ParseError:
                pass  # sunucu şemayı zorlamadı: yedek yola düş
        # Yedek: şemayı talimatla ver, doğrula, bir kez düzeltme iste.
        guide = Message(
            "system",
            "Yalnızca şu JSON şemasına uyan tek bir JSON nesnesi döndür, başka metin yazma:\n"
            + json.dumps(json_schema, ensure_ascii=False),
        )
        msgs = [guide, *messages]
        completion, _ = await self._call(role, msgs, max_tokens, reasoning_effort)
        try:
            return Typed(value=_parse(schema, completion.text), completion=completion)
        except _ParseError as err:
            fix = [
                *msgs,
                Message("assistant", completion.text),
                Message("user", f"Cevap şemaya uymadı: {err}. Yalnızca düzeltilmiş JSON'u yaz."),
            ]
            second, _ = await self._call(role, fix, max_tokens, reasoning_effort)
            try:
                value = _parse(schema, second.text)
            except _ParseError as err2:
                raise ModelError(f"cevap şemaya uymadı ({schema.__name__}): {err2}") from err2
            return Typed(value=value, completion=second, repaired=True)

    async def complete_tools(
        self,
        role: str,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int | None = None,
    ) -> ToolTurn:
        """Araç döngüsünün bir turu (ADR-031): önce native tool calling; sunucu reddederse ya da
        geçersiz çağrı üretirse tipli JSON yedeği, sonuç model başına hatırlanır."""
        if self._cap(role, "tools") is not False:
            wire = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            try:
                completion, choice = await self._call(
                    role, messages, max_tokens, None, extra={"tools": wire, "tool_choice": "auto"}
                )
            except _HttpError as exc:
                if exc.status not in (400, 404, 422):
                    raise ModelError(str(exc)) from exc
                self._learn(role, "tools", False)
            else:
                calls = _tool_calls(choice, {t.name for t in tools})
                if calls is not None:
                    self._learn(role, "tools", True)
                    return ToolTurn(completion.text, calls, completion, "native")
                self._learn(role, "tools", False)
        return await self._tools_json(role, messages, tools, max_tokens)

    async def _tools_json(
        self, role: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int | None
    ) -> ToolTurn:
        names = {t.name for t in tools}

        class _Step(BaseModel):
            action: str  # "call" ya da "done"
            tool: str | None = None
            arguments: dict[str, Any] = {}
            note: str = ""

        catalog = "\n".join(
            f"- {t.name}: {t.description} ŞEMA: {json.dumps(t.parameters, ensure_ascii=False)}"
            for t in tools
        )
        ask = Message(
            "user",
            "Kullanabileceğin araçlar:\n"
            f"{catalog}\n"
            'Sıradaki adımı ver: {"action": "call", "tool": <ad>, "arguments": {...}, "note": '
            '<neden>} ya da işin bittiyse {"action": "done", "note": <kısa analiz>}.',
        )
        typed = await self.complete_typed(
            role, [*(_as_text(m) for m in messages), ask], _Step, max_tokens=max_tokens
        )
        step = typed.value
        if step.action == "call":
            if step.tool not in names:
                raise ModelError(f"bilinmeyen araç: {step.tool}")
            req = ToolRequest(f"json-{len(messages)}", step.tool, dict(step.arguments))
            return ToolTurn(step.note, (req,), typed.completion, "json")
        return ToolTurn(step.note, (), typed.completion, "json")

    async def classify(
        self, role: str, messages: list[Message], labels: list[str]
    ) -> Classification:
        """Seçenekleri harflerle sorar; ilk token'ın logprob'larından dağılım çıkarır.

        Sunucu logprob vermiyorsa tipli cevaba (etiket + güven) düşer. Düşünme kapalıdır.
        """
        if not 2 <= len(labels) <= len(string.ascii_uppercase):
            raise ValueError("2-26 etiket gerekli")
        letters = {string.ascii_uppercase[i]: lab for i, lab in enumerate(labels)}
        options = "\n".join(f"{k}) {v}" for k, v in letters.items())
        ask = Message(
            "user",
            f"Seçenekler:\n{options}\nYalnızca seçeneğin harfini yaz ({', '.join(letters)}).",
        )
        if self._cap(role, "logprobs") is not False:
            try:
                completion, choice = await self._call(
                    role,
                    [*messages, ask],
                    max_tokens=1,
                    effort="none",
                    extra={"logprobs": True, "top_logprobs": 20},
                )
            except _HttpError as exc:
                if exc.status != 400:
                    raise ModelError(str(exc)) from exc
                self._learn(role, "logprobs", False)
            else:
                probs = _letter_probs(choice, letters)
                if probs is not None:
                    self._learn(role, "logprobs", True)
                    return Classification(probs=probs, method="logprobs", completion=completion)
                self._learn(role, "logprobs", False)

        class _Pick(BaseModel):
            label: str
            confidence: float

        typed = await self.complete_typed(
            role,
            [*messages, Message("user", f"Seçenekler: {labels}. Etiketi ve 0-1 arası güveni ver.")],
            _Pick,
            reasoning_effort="none",
        )
        pick = typed.value
        if pick.label not in labels:
            raise ModelError(f"tanımsız etiket: {pick.label}")
        conf = min(max(pick.confidence, 0.0), 1.0)
        rest = (1 - conf) / (len(labels) - 1)
        probs = {lab: (conf if lab == pick.label else rest) for lab in labels}
        return Classification(probs=probs, method="typed", completion=typed.completion)


def _retry_after(resp: httpx.Response) -> float | None:
    """Retry-After (saniye) ya da Azure'un retry-after-ms başlığı."""
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        v = resp.headers.get(name)
        if v:
            try:
                return float(v) * scale
            except ValueError:
                return None
    return None


def _tool_calls(choice: dict[str, Any], names: set[str]) -> tuple[ToolRequest, ...] | None:
    """Native çağrıları ayrıştırır; bilinmeyen araç ya da bozuk JSON → None (yedeğe düşülür)."""
    raw = (choice.get("message") or {}).get("tool_calls") or []
    out = []
    for i, c in enumerate(raw):
        fn = c.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            return None
        if fn.get("name") not in names or not isinstance(args, dict):
            return None
        out.append(ToolRequest(str(c.get("id") or f"call-{i}"), fn["name"], args))
    return tuple(out)


class _HttpError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"HTTP {status}: {text}")
        self.status = status


class _ParseError(Exception):
    pass


def _parse(schema: type[T], text: str) -> T:
    raw = _FENCE.sub("", text.strip())
    try:
        return schema.model_validate_json(raw)
    except ValidationError as exc:
        raise _ParseError(str(exc).splitlines()[0] if str(exc) else "geçersiz JSON") from exc


def _letter_probs(choice: dict[str, Any], letters: dict[str, str]) -> dict[str, float] | None:
    content = (choice.get("logprobs") or {}).get("content") or []
    if not content:
        return None
    scores: dict[str, float] = {}
    for alt in content[0].get("top_logprobs") or []:
        tok = str(alt.get("token", "")).strip().strip(")").upper()
        if tok in letters and tok not in scores:
            scores[tok] = math.exp(float(alt["logprob"]))
    total = sum(scores.values())
    if total <= 0:
        return None
    return {letters[k]: scores.get(k, 0.0) / total for k in letters}
