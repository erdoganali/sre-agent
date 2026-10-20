"""Model kataloğu (ADR-027).

`models:` model anahtarı → adres ve özellikler; `roles:` kullanım yeri → model ve istek ayarları.
Her metin değeri `${ORTAM_DEGISKENI:varsayilan}` ile ezilebilir (iç içe kullanılabilir).
Model değiştirmek ya da araya bir gateway koymak yalnızca bu dosya / ortam değişkeni işidir.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_ENV = re.compile(r"\$\{([^{}]+)\}")

# Düşük → yüksek. "none": düşünme kapalı.
EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]


def resolve_env(value: Any) -> Any:
    """`${AD}` / `${AD:varsayilan}` ifadelerini içten dışa çözer; dict/list içinde de."""
    if isinstance(value, str):
        while m := _ENV.search(value):
            name, _, default = m.group(1).partition(":")
            value = value[: m.start()] + os.environ.get(name, default) + value[m.end() :]
        return value
    if isinstance(value, dict):
        return {k: resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env(v) for v in value]
    return value


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str  # sunucuya giden model adı
    url: str  # tam uç nokta, ör. http://host:port/v1/chat/completions (gateway yolu da olabilir)
    provider: str = "openai-compatible"  # bu fazda tek desteklenen
    max_context_tokens: int
    tokenizer_dir_name: str | None = None  # imajdaki tokenizer klasörü (T2.2)
    timeout_s: float = 120  # cevap süresi (uzun üretimler)
    connect_timeout_s: float = 5  # bağlantı kurulamıyorsa uzun beklenmez
    retries: int = 2
    # Aynı anda bu modele giden en çok istek (ör. gateway kotası); boşsa sınır yok
    max_concurrency: int | None = Field(default=None, ge=1)
    headers: dict[str, str] = Field(default_factory=dict)  # boş değerli başlık gönderilmez
    # Desteklenen reasoning seviyeleri; desteklenmeyen istek bir alt seviyeye indirilir.
    supported_reasoning_efforts: list[str] = Field(default_factory=list)
    # reasoning_effort'u istek gövdesine yaz (vLLM / gpt-oss); qwen için false, anahtar kullanılır
    send_reasoning_effort: bool = True
    # Düşünmeyi açıp kapatan alan, nokta yollu (ör. chat_template_kwargs.enable_thinking)
    thinking_switch: str | None = None
    sampling_params: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> ModelSpec:
        if self.provider != "openai-compatible":
            raise ValueError(f"desteklenmeyen provider: {self.provider} (ADR-027)")
        unknown = set(self.supported_reasoning_efforts) - set(EFFORT_ORDER)
        if unknown:
            raise ValueError(f"bilinmeyen reasoning seviyesi: {sorted(unknown)}")
        return self

    def clamp_effort(self, effort: str | None) -> str | None:
        """İstenen seviyeyi desteklenen en yakın alt seviyeye indirir; 'none' olduğu gibi kalır."""
        if effort is None or effort == "none":
            return effort
        if effort not in EFFORT_ORDER:
            raise ValueError(f"bilinmeyen reasoning seviyesi: {effort}")
        if not self.supported_reasoning_efforts:
            return None
        rank = EFFORT_ORDER.index(effort)
        lower = [e for e in self.supported_reasoning_efforts if EFFORT_ORDER.index(e) <= rank]
        pool = lower or self.supported_reasoning_efforts
        return max(pool, key=EFFORT_ORDER.index) if lower else min(pool, key=EFFORT_ORDER.index)


class RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    sampling_params: dict[str, Any] = Field(default_factory=dict)  # modelinkini ezer


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: dict[str, ModelSpec]
    roles: dict[str, RoleSpec]

    @model_validator(mode="after")
    def _roles_point_to_models(self) -> Catalog:
        missing = {r: s.model for r, s in self.roles.items() if s.model not in self.models}
        if missing:
            raise ValueError(f"rollerin modeli katalogda yok: {missing}")
        return self

    def resolve(self, role: str) -> tuple[str, ModelSpec, RoleSpec]:
        if role not in self.roles:
            raise KeyError(f"tanımsız rol: {role} (tanımlı: {sorted(self.roles)})")
        r = self.roles[role]
        return r.model, self.models[r.model], r


def load_catalog(path: Path) -> Catalog:
    return Catalog.model_validate(resolve_env(yaml.safe_load(path.read_text()) or {}))
