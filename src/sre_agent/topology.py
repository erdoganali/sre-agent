"""Ürün topology'si (T3.3): bileşenler, pod eşlemesi, bağımlılıklar, canlı kaynaktan izinli alanlar.

Veri paketin `topology.yaml` dosyasında; çekirdek yalnız biçimi bilir. Canlı kaynaktan (ör. ürünün
custom resource'u) yalnız `live.fields` listesindeki yollar okunur: kaynakta parola gibi alanlar
olabilir, kaynak bütünüyle döndürülmez.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_PATH = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+|\.\*)*$")


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pods: str  # pod adına uyan regex (search)
    role: str = ""
    depends: tuple[str, ...] = ()  # bileşen ya da dış bağımlılık adları


class External(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = ""


class LiveSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group: str
    kind: str
    # Okunacak yollar, ör. "spec.dependencies.etcd.endpoints"; "*" bir sözlüğün bütün anahtarları
    fields: tuple[str, ...]

    @model_validator(mode="after")
    def _paths(self) -> LiveSource:
        bad = [f for f in self.fields if not _PATH.match(f)]
        if bad:
            raise ValueError(f"geçersiz alan yolları: {bad}")
        return self


class Topology(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    components: dict[str, Component] = Field(min_length=1)
    externals: dict[str, External] = Field(default_factory=dict)
    live: LiveSource | None = None

    @model_validator(mode="after")
    def _refs(self) -> Topology:
        known = set(self.components) | set(self.externals)
        for name, c in self.components.items():
            re.compile(c.pods)
            unknown = set(c.depends) - known
            if unknown:
                raise ValueError(f"{name}: bilinmeyen bağımlılık {sorted(unknown)}")
        return self

    def component_of(self, pod: str) -> str | None:
        return next((n for n, c in self.components.items() if re.search(c.pods, pod)), None)

    def dependents(self, name: str) -> list[str]:
        """`name`'e doğrudan ya da dolaylı bağlı bileşenler."""
        out: set[str] = set()
        frontier = {name}
        while frontier:
            nxt = {n for n, c in self.components.items() if set(c.depends) & frontier} - out
            out |= nxt
            frontier = nxt
        return sorted(out - {name})


def load(path: Path) -> Topology | None:
    return Topology.model_validate(yaml.safe_load(path.read_text())) if path.is_file() else None


def pick(obj: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Yalnız izinli yolları çıkarır; '*' bir sözlüğün her anahtarında devam eder."""
    out: dict[str, Any] = {}
    for path in fields:
        _pick(obj, path.split("."), "", out)
    return out


def _pick(node: Any, parts: list[str], prefix: str, out: dict[str, Any]) -> None:
    if not parts:
        if not isinstance(node, (dict, list)) or (
            isinstance(node, list) and all(not isinstance(x, (dict, list)) for x in node)
        ):
            out[prefix] = node
        return
    head, rest = parts[0], parts[1:]
    if not isinstance(node, Mapping):
        return
    keys = list(node) if head == "*" else [head]
    for k in keys:
        if k in node:
            _pick(node[k], rest, f"{prefix}.{k}" if prefix else k, out)
