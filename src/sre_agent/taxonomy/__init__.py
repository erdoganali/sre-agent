"""Kök neden taksonomisi: gruplar, kategoriler ve ayırt etme kuralları.

Veri `taxonomy.yaml`'da; bu modül onu yükler ve bütünlüğünü doğrular.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Slug = Field(pattern=r"^[a-z][a-z0-9_]*$")
SemVer = Field(pattern=r"^\d+\.\d+\.\d+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Group(_Strict):
    id: str = Slug
    name: str = Field(min_length=1)


class DistinguishingRule(_Strict):
    """Ayırt etme kuralı: şu durumda bu kategori değil, şu."""

    when: str = Field(min_length=1)
    not_this_but: str = Slug
    why: str = Field(min_length=1)


class Category(_Strict):
    id: str = Slug
    name: str = Field(min_length=1)
    group: str = Slug
    definition: str = Field(min_length=1)
    rules: tuple[DistinguishingRule, ...] = Field(min_length=1)


class Unknown(_Strict):
    id: Literal["unknown"]
    definition: str = Field(min_length=1)


class Taxonomy(_Strict):
    version: str = SemVer
    groups: tuple[Group, ...] = Field(min_length=1)
    categories: tuple[Category, ...] = Field(min_length=1)
    unknown: Unknown

    @model_validator(mode="after")
    def _check_references(self) -> Taxonomy:
        group_ids = [g.id for g in self.groups]
        category_ids = [c.id for c in self.categories]
        if len(set(group_ids)) != len(group_ids):
            raise ValueError("grup kimlikleri benzersiz değil")
        if len(set(category_ids)) != len(category_ids):
            raise ValueError("kategori kimlikleri benzersiz değil")
        if self.unknown.id in category_ids:
            raise ValueError("'unknown' bir kategori kimliği olamaz")

        known = set(category_ids)
        for c in self.categories:
            if c.group not in group_ids:
                raise ValueError(f"{c.id}: bilinmeyen grup {c.group!r}")
            for r in c.rules:
                if r.not_this_but == c.id:
                    raise ValueError(f"{c.id}: kural kategorinin kendisini gösteriyor")
                if r.not_this_but not in known:
                    raise ValueError(
                        f"{c.id}: kural bilinmeyen kategoriyi gösteriyor {r.not_this_but!r}"
                    )

        empty = set(group_ids) - {c.group for c in self.categories}
        if empty:
            raise ValueError(f"kategorisi olmayan grup: {sorted(empty)}")
        return self

    def category(self, category_id: str) -> Category:
        for c in self.categories:
            if c.id == category_id:
                return c
        raise KeyError(category_id)

    def in_group(self, group_id: str) -> tuple[Category, ...]:
        return tuple(c for c in self.categories if c.group == group_id)


def parse(text: str) -> Taxonomy:
    return Taxonomy.model_validate(yaml.safe_load(text))


@cache
def load() -> Taxonomy:
    """Paketle gelen taksonomiyi yükler (bir kez)."""
    return parse(files(__package__).joinpath("taxonomy.yaml").read_text(encoding="utf-8"))


def guide(taxonomy: Taxonomy) -> str:
    """Bütün kategoriler: kimlik, tanım ve ayırt etme kuralları (model prompt'ları için)."""
    out = []
    for c in taxonomy.categories:
        out.append(f"- {c.id}: {c.definition}")
        out += [f"  · {r.when} ise bu değil, {r.not_this_but} ({r.why})" for r in c.rules]
    out.append(f"- {taxonomy.unknown.id}: {taxonomy.unknown.definition}")
    return "\n".join(out)
