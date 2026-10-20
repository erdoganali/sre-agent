"""Bilgi tabanı (RAG): paketlerin arıza kataloğu, uzman rehberleri ve taksonomi.

Belgeler repodan üretilir ve bir vektör veritabanında BM25 full-text ile aranır (adaptör:
`adapters/vector_store.py`): embedding modeli gerekmez (air-gapped; gateway'de embedding yok).
Kaynak repo, veritabanı yalnız indeks.

Bilgi tabanının içeriği güvenilir veridir (repodan gelir); log ve araç çıktısı buraya yazılmaz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from sre_agent import catalog
from sre_agent.taxonomy import load as load_taxonomy

TEXT_MAX = 8000
KINDS = ("failure", "guide", "category")


class KnowledgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str | None = None  # vektör veritabanı adresi ya da yerel dosya; boşsa kapalı
    token_env: str = "KNOWLEDGE_TOKEN"  # "kullanıcı:parola" ya da token; ortam değişkeninden
    collection: str = "sre_knowledge"
    top_k: int = 3  # graph'ın brief'e eklediği bilinen arıza sayısı


@dataclass(frozen=True)
class Doc:
    id: str
    kind: str  # failure | guide | category
    title: str
    category: str
    text: str
    pack: str = ""


@dataclass(frozen=True)
class Hit:
    doc: Doc
    score: float


def _failures(pack: str, pack_dir: Path) -> list[Doc]:
    if not (pack_dir / catalog.CATALOG_FILE).is_file():
        return []
    cat = catalog.load(pack_dir)
    out = []
    for e in cat.entries:
        signals = "; ".join(
            " ".join(x for x in (s.metric, s.logql, s.object, s.note) if x) for s in e.signals
        )
        text = "\n".join(
            x
            for x in (
                e.title,
                f"Alan: {cat.areas.get(e.area, e.area)}. Etki: {e.scope}.",
                f"Belirti: {e.symptom}",
                f"Sinyaller: {signals}",
                f"Eşik: {e.threshold}" if e.threshold else "",
                f"Alarmlar: {', '.join(e.alerts)}" if e.alerts else "",
                f"Kontroller: {', '.join(e.checks)}" if e.checks else "",
                f"Not: {e.notes}" if e.notes else "",
            )
            if x
        )
        out.append(Doc(f"{pack}:failure:{e.id}", "failure", e.title, e.category, text, pack))
    return out


def _guides(pack: str, pack_dir: Path) -> list[Doc]:
    out = []
    for f in sorted((pack_dir / "specialists").glob("*.md")):
        text = f.read_text(encoding="utf-8").strip()
        if text:
            title = f"{f.stem} uzmanı rehberi"
            out.append(Doc(f"{pack}:guide:{f.stem}", "guide", title, "", text, pack))
    return out


def _categories() -> list[Doc]:
    out = []
    for c in load_taxonomy().categories:
        rules = " ".join(f"{r.when} ise {r.not_this_but} ({r.why})." for r in c.rules)
        text = f"{c.name}: {c.definition}\nAyırt etme: {rules}"
        out.append(Doc(f"taxonomy:{c.id}", "category", c.name, c.id, text))
    return out


def collect(packs: dict[str, Path]) -> list[Doc]:
    """Paket adı → dizin; taksonomi her zaman eklenir."""
    docs = [
        d for name, path in packs.items() for d in (*_failures(name, path), *_guides(name, path))
    ]
    return docs + _categories()


def query_text(q: str) -> str:
    # Alarm ve kontrol adları (PodCrashLooping, pod_restarts) analizörde tek kelime kalmasın
    q = re.sub(r"([a-z])([A-Z])", r"\1 \2", q)
    return q.replace("_", " ").replace("-", " ")


class KnowledgeStore(Protocol):
    """Bilgi tabanı arka ucu (adaptör: `adapters/vector_store.py`)."""

    def rebuild(self, docs: list[Doc]) -> int: ...

    def search_sync(self, query: str, limit: int = 5, kind: str | None = None) -> list[Hit]: ...

    async def search(self, query: str, limit: int = 5, kind: str | None = None) -> list[Hit]: ...


def brief_section(hits: list[Hit]) -> str:
    """Graph brief'ine eklenen bölüm: bilinen arızalar, kısa."""
    if not hits:
        return ""
    lines = ["Bilgi tabanında benzer bilinen arızalar (RAG; olayla eşleştiğini kanıtla doğrula):"]
    for h in hits:
        body = h.doc.text.split("\n")
        symptom = next((x for x in body if x.startswith("Belirti:")), "")[:300]
        checks = next((x for x in body if x.startswith("Kontroller:")), "")
        lines.append(f"- {h.doc.title} [{h.doc.category}] {symptom} {checks}".rstrip())
    return "\n".join(lines)
