"""Model portu: ajanlar modele yalnızca bu arayüzle erişir (ADR-003, ADR-027).

Model, sağlayıcı ve adres bilgisi kodda değil, model kataloğundadır (`config/models.yaml`).
Çağıran taraf modeli değil rolü seçer (ör. `decision`, `specialist`, `synthesize`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ToolRequest:
    """Modelin istediği araç çağrısı (ADR-031)."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: tuple[ToolRequest, ...] = ()  # assistant: istenen araç çağrıları
    tool_call_id: str | None = None  # tool: hangi çağrının sonucu


@dataclass(frozen=True)
class ToolSpec:
    """Modele sunulan araç: ad, açıklama, JSON şeması (MCP aracının input_schema'sı)."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolTurn:
    """Araç döngüsünün bir turu: model ya araç çağrıları ister ya da bitirir (calls boş)."""

    text: str
    calls: tuple[ToolRequest, ...]
    completion: Completion
    method: Literal["native", "json"]


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0  # prompt_tokens'ın sunucu önbelleğinden gelen kısmı (prefix caching)

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class Completion:
    """Model cevabı. `reasoning` düşünme metnidir: kanıt sayılmaz, rapora girmez."""

    text: str
    model: str
    usage: Usage
    latency_s: float
    reasoning: str | None = None
    finish_reason: str | None = None


@dataclass(frozen=True)
class Typed[T2: BaseModel]:
    value: T2
    completion: Completion
    repaired: bool = False  # ilk cevap şemaya uymadı, düzeltme isteğiyle alındı


@dataclass(frozen=True)
class Classification:
    """Olasılık dağılımı (toplamı 1). `method`: logprobs ya da tipli cevaptan türetilmiş."""

    probs: dict[str, float]
    method: Literal["logprobs", "typed"]
    completion: Completion
    extra: dict[str, float] = field(default_factory=dict)

    @property
    def top(self) -> tuple[str, float]:
        label = max(self.probs, key=lambda k: self.probs[k])
        return label, self.probs[label]


class ModelError(Exception):
    """Model çağrısı sonuçsuz kaldı (erişim, zaman aşımı, şemaya uymayan cevap)."""


@runtime_checkable
class ModelPort(Protocol):
    async def complete(
        self,
        role: str,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Completion: ...

    async def complete_typed(
        self,
        role: str,
        messages: list[Message],
        schema: type[T],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Typed[T]: ...

    async def classify(
        self, role: str, messages: list[Message], labels: list[str]
    ) -> Classification: ...


@runtime_checkable
class ToolModelPort(Protocol):
    """Araç döngüsü (ADR-031): native tool calling, desteklenmiyorsa tipli JSON yedeği."""

    async def complete_tools(
        self,
        role: str,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int | None = None,
    ) -> ToolTurn: ...
