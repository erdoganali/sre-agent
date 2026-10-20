"""Domain paketleri: ortama/ürüne özgü kontroller çekirdeğe yalnızca yapılandırmayla eklenir.

Paket dizini:
  pack.yaml      manifest (ad, sürüm, modül, alarm → kontrol eşlemesi)
  <module>/      Python paketi; `PACK: PackContent` dışa verir
  rbac-values.yaml  (isteğe bağlı) sre-agent-rbac chart'ına ek okuma kuralları
  topology.yaml  (isteğe bağlı) ürünün bileşenleri ve bağımlılıkları (sre_agent.topology)
  specialists/<ad>.md  (isteğe bağlı) specialist yönergesi, ör. product.md, k8s.md (T3.4)

Manifest ayrıca ürüne özel tazelik sorgularını (`freshness`), ürünün alarmlarını sre-agent'a
yönlendiren Alertmanager eşleyicilerini (`alert_matchers`) ve kategori notlarını
(`category_notes`: kategori kimliği → ürüne özel örnekler) taşır. Çekirdek ürün adı bilmez.

Paketler çekirdeğin wheel'ine girmez; dosya yolundan import edilir. Yapılandırmada listelenmeyen
paket hiç import edilmez; paket dizini kaldırılsa bile çekirdek kontroller çalışır.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sre_agent.checks import Check
from sre_agent.checks.builtin import BUILTIN_CHECKS
from sre_agent.checks.builtin.capacity import DEFAULT_SIGNALS, Signal
from sre_agent.freshness import FreshnessProbe
from sre_agent.taxonomy import load as load_taxonomy
from sre_agent.topology import Topology
from sre_agent.topology import load as load_topology

# Eşlemede "bütün çekirdek kontroller" kısaltması: [@core, <paket kontrolü>] = çekirdek + paket
CORE = "@core"

CustomKind = tuple[str, str, str, str]  # (grup, sürüm, kind, plural)


@dataclass(frozen=True)
class PackContent:
    """Paket modülünün dışa verdiği içerik."""

    checks: Sequence[type[Check]] = ()
    signals: Sequence[Signal] = ()  # golden_signals_baseline'a eklenecek sinyaller
    custom_kinds: Sequence[CustomKind] = ()  # K8sPort.list_custom ile okunabilecek türler


class PackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = ""
    module: str = Field(pattern=r"^[a-z_][a-z0-9_]*$")
    # Alarm adı (regex, tam eşleşme) → çalıştırılacak kontrol adları (çekirdek ya da paket)
    alert_routes: dict[str, list[str]] = Field(default_factory=dict)
    # Ürüne özel veri tazeliği sorguları (yapılandırmadakilerle birleşir)
    freshness: list[FreshnessProbe] = Field(default_factory=list)
    # Ürünün alarmlarını sre-agent'a yönlendiren Alertmanager eşleyicileri, ör. 'team="urun"'
    alert_matchers: list[str] = Field(default_factory=list)
    # Kategori kimliği → ürüne özel açıklama (etiketleme formunda gösterilir)
    category_notes: dict[str, str] = Field(default_factory=dict)
    # Investigation graph'ta modelin ürün specialist'i için kullanabileceği adlar (T3.2)
    specialist_aliases: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Pack:
    manifest: PackManifest
    content: PackContent
    path: Path
    topology: Topology | None = None
    guides: Mapping[str, str] = field(default_factory=dict)  # specialist adı → yönerge (T3.4)


class PackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


class Config(BaseModel):
    """Çekirdek yapılandırmasının paketlerle ilgili kısmı (T1.7'de genişler)."""

    model_config = ConfigDict(extra="allow")

    packs: list[PackConfig] = Field(default_factory=list)


def _import(path: Path, manifest: PackManifest) -> ModuleType:
    module_dir = path / manifest.module
    init = module_dir / "__init__.py"
    if not init.is_file():
        raise FileNotFoundError(f"{manifest.name}: paket modülü yok: {init}")
    name = f"sre_agent_packs.{manifest.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(
        name, init, submodule_search_locations=[str(module_dir)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"{manifest.name}: modül yüklenemedi")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_pack(path: Path) -> Pack:
    manifest = PackManifest.model_validate(yaml.safe_load((path / "pack.yaml").read_text()))
    content = getattr(_import(path, manifest), "PACK", None)
    if not isinstance(content, PackContent):
        raise TypeError(f"{manifest.name}: modül PACK: PackContent dışa vermiyor")
    guides = {g.stem: g.read_text().strip() for g in sorted((path / "specialists").glob("*.md"))}
    return Pack(
        manifest=manifest,
        content=content,
        path=path,
        topology=load_topology(path / "topology.yaml"),
        guides=guides,
    )


def load_packs(config: Config, base: Path) -> list[Pack]:
    """Yapılandırmadaki paketleri yükler; göreli yollar `base`'e göre çözülür."""
    return [load_pack((base / p.path).resolve()) for p in config.packs]


def load_config(path: Path) -> Config:
    return Config.model_validate(yaml.safe_load(path.read_text()) or {})


@dataclass
class CheckRegistry:
    """Çekirdek + paket kontrolleri, sinyaller, custom türler ve alarm eşlemeleri."""

    packs: Sequence[Pack] = ()
    checks: dict[str, type[Check]] = field(init=False)

    def __post_init__(self) -> None:
        self.checks = {}
        for cls in [*BUILTIN_CHECKS, *(c for p in self.packs for c in p.content.checks)]:
            if cls.name in self.checks and self.checks[cls.name] is not cls:
                raise ValueError(f"kontrol adı çakışıyor: {cls.name}")
            self.checks[cls.name] = cls
        known = {c.id for c in load_taxonomy().categories}
        for p in self.packs:
            unknown_cats = set(p.manifest.category_notes) - known
            if unknown_cats:
                raise ValueError(f"{p.manifest.name}: bilinmeyen kategoriler {unknown_cats}")
            for pattern, names in p.manifest.alert_routes.items():
                re.compile(pattern)
                unknown = set(names) - set(self.checks) - {CORE}
                if unknown:
                    raise ValueError(
                        f"{p.manifest.name}: {pattern} bilinmeyen kontroller {unknown}"
                    )

    @property
    def signals(self) -> tuple[Signal, ...]:
        return (*DEFAULT_SIGNALS, *(s for p in self.packs for s in p.content.signals))

    @property
    def freshness(self) -> tuple[FreshnessProbe, ...]:
        return tuple(f for p in self.packs for f in p.manifest.freshness)

    @property
    def category_notes(self) -> dict[str, list[tuple[str, str]]]:
        """Kategori → [(paket adı, not)]."""
        out: dict[str, list[tuple[str, str]]] = {}
        for p in self.packs:
            for cat, note in p.manifest.category_notes.items():
                out.setdefault(cat, []).append((p.manifest.name, note))
        return out

    def packs_for_alert(self, alertname: str) -> list[str]:
        """Eşlemesi bu alarm adına uyan paketlerin adları."""
        return [
            p.manifest.name
            for p in self.packs
            if any(re.fullmatch(pattern, alertname) for pattern in p.manifest.alert_routes)
        ]

    @property
    def product_checks(self) -> frozenset[str]:
        """Paketlerin kendi kontrolleri (graph'ta ürün specialist'inin kanıtı)."""
        return frozenset(c.name for p in self.packs for c in p.content.checks)

    @property
    def product_aliases(self) -> frozenset[str]:
        return frozenset(
            a for p in self.packs for a in (p.manifest.name, *p.manifest.specialist_aliases)
        )

    @property
    def specialist_guides(self) -> dict[str, str]:
        """Specialist adı → paketlerin yönergeleri (birden çok paket alt alta)."""
        out: dict[str, list[str]] = {}
        for p in self.packs:
            for name, text in p.guides.items():
                out.setdefault(name, []).append(f"[{p.manifest.name}] {text}")
        return {n: "\n\n".join(t) for n, t in out.items()}

    @property
    def custom_kinds(self) -> tuple[CustomKind, ...]:
        return tuple(k for p in self.packs for k in p.content.custom_kinds)

    def routes(self) -> Mapping[str, list[str]]:
        """Paketlerin alarm → kontrol eşlemeleri (T1.7'de kontrol seçimi için)."""
        merged: dict[str, list[str]] = {}
        for p in self.packs:
            for pattern, names in p.manifest.alert_routes.items():
                merged.setdefault(pattern, []).extend(
                    n for n in names if n not in merged.get(pattern, [])
                )
        return merged

    def for_alert(self, alertname: str) -> list[str]:
        names = {
            n
            for pattern, routed in self.routes().items()
            if re.fullmatch(pattern, alertname)
            for n in routed
        }
        if CORE in names:
            names = (names - {CORE}) | {c.name for c in BUILTIN_CHECKS}
        return sorted(names)
