"""Arıza kataloğu: bir paketin "ne bozulabilir, nasıl görünür, kim yakalar" tablosu.

Kaynak paketteki `catalog.yaml`; okunur hâli (`catalog.md`) ondan üretilir. Doğrulama iki katmanlı:
- çevrimdışı (test): kategori, kontrol ve alarm adları var mı; metrik adları envanterde mi
- canlı (`make catalog-check`): metrik beyan edilen yolda gerçekten görülüyor mu (salt okunur)

Kullanım:
  python -m sre_agent.catalog render <paket-dizini> [--check]
  python -m sre_agent.catalog verify <paket-dizini> --prometheus <url>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sre_agent.taxonomy import load as load_taxonomy

CATALOG_FILE = "catalog.yaml"
RENDERED_FILE = "catalog.md"


class SignalPath(StrEnum):
    """Sinyalin bize ulaştığı yol."""

    PIPELINE = "pipeline"  # ürünün telemetri hattından geliyor
    SOURCE = "source"  # ürünün kaynağında tanımlı; olay olmadan oluşmuyor, canlıda görülmedi
    PLATFORM = "platform"  # Kubernetes/altyapı metriği, doğrudan toplanıyor
    LOG = "log"  # log sorgusu (LogQL)
    K8S = "k8s"  # Kubernetes nesnesinin durumu (API), metrik değil
    NONE = "none"  # hiçbir yoldan gelmiyor: boşluk


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str | None = None
    logql: str | None = None
    object: str | None = None  # K8S yolunda nesne türü ve alanı, ör. "Backup.status.phase"
    path: SignalPath
    note: str = ""

    @model_validator(mode="after")
    def _one_kind(self) -> Signal:
        kinds = [k for k in (self.metric, self.logql, self.object) if k]
        if len(kinds) != 1:
            raise ValueError("sinyal tam olarak birini taşır: metric, logql ya da object")
        if self.logql and self.path is not SignalPath.LOG:
            raise ValueError("logql sinyalinin yolu log olmalı")
        if self.object and self.path is not SignalPath.K8S:
            raise ValueError("object sinyalinin yolu k8s olmalı")
        if self.metric and self.path in (SignalPath.LOG, SignalPath.K8S):
            raise ValueError("metrik sinyalinin yolu log ya da k8s olamaz")
        return self

    @property
    def label(self) -> str:
        return self.metric or self.logql or self.object or ""


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    area: str
    title: str
    symptom: str
    signals: list[Signal] = Field(min_length=1)
    threshold: str = ""
    threshold_source: str = ""
    alerts: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    scenario: str | None = None  # senaryo dizini adı (scenarios/<ad>); yoksa planlı sayılır
    panel: str | None = None
    category: str
    # Etki alanı: tek tenant, bütün tenant'lar (ortak anahtar, operatör) ya da platformun kendisi
    scope: Literal["tenant", "tum-tenantlar", "platform"] = "tenant"
    notes: str = ""
    refs: list[str] = Field(default_factory=list)


_SCOPE_TEXT = {
    "tenant": "tek tenant",
    "tum-tenantlar": "**bütün tenant'lar**",
    "platform": "platform",
}


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    title: str
    intro: str = ""
    # Canlı doğrulamada PIPELINE yolunun seçicisi, ör. '{job="..."}'
    pipeline_selector: str
    alerts_source: str  # repo köküne göre alarm kuralları dosyası
    inventory: str  # paket dizinine göre metrik envanteri
    dashboards: list[str] = Field(default_factory=list)  # paket dizinine göre dashboard JSON'ları
    areas: dict[str, str]  # alan kimliği → başlık (sıra buradan)
    known_issues: list[str] = Field(default_factory=list)
    # Paket kontrollerinde bilerek envanter dışı kalan metrik adları (known_issues'ta açıklanır)
    unverified_check_metrics: list[str] = Field(default_factory=list)
    entries: list[Entry]


def load(pack_dir: Path) -> Catalog:
    return Catalog.model_validate(yaml.safe_load((pack_dir / CATALOG_FILE).read_text()))


# ---------------------------------------------------------------- çevrimdışı doğrulama


def read_inventory(path: Path) -> dict[str, set[str]]:
    """Envanter: her satır `ad<TAB>işaretler` (virgüllü: source, pipeline, direct)."""
    out: dict[str, set[str]] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, _, flags = line.partition("\t")
        out[name.strip()] = {f.strip() for f in flags.split(",") if f.strip()}
    return out


def alert_names(repo_root: Path, source: str) -> set[str]:
    text = (repo_root / source).read_text()
    return set(re.findall(r"-\s+alert:\s*([A-Za-z0-9_]+)", text))


def panel_titles(pack_dir: Path, dashboards: list[str]) -> set[str]:
    titles: set[str] = set()
    for rel in dashboards:
        data = json.loads((pack_dir / rel).read_text())
        titles |= {p.get("title", "") for p in data.get("panels", []) if p.get("type") != "row"}
    return titles


def problems(cat: Catalog, pack_dir: Path, repo_root: Path, check_names: set[str]) -> list[str]:
    """Katalogdaki kırık bağlantılar; boş liste = temiz."""
    out: list[str] = []
    categories = {c.id for c in load_taxonomy().categories}
    alerts = alert_names(repo_root, cat.alerts_source)
    inventory = read_inventory(pack_dir / cat.inventory)
    panels = panel_titles(pack_dir, cat.dashboards)
    ids: set[str] = set()
    for e in cat.entries:
        if e.id in ids:
            out.append(f"{e.id}: kimlik tekrar ediyor")
        ids.add(e.id)
        if e.area not in cat.areas:
            out.append(f"{e.id}: bilinmeyen alan {e.area}")
        if e.category not in categories:
            out.append(f"{e.id}: bilinmeyen kategori {e.category}")
        out += [f"{e.id}: bilinmeyen kontrol {c}" for c in e.checks if c not in check_names]
        out += [f"{e.id}: bilinmeyen alarm {a}" for a in e.alerts if a not in alerts]
        if e.panel and e.panel not in panels:
            out.append(f"{e.id}: dashboard'da olmayan panel {e.panel!r}")
        for s in e.signals:
            if s.metric and s.path in (SignalPath.PIPELINE, SignalPath.SOURCE):
                flags = inventory.get(s.metric)
                if flags is None:
                    out.append(f"{e.id}: {s.metric} envanterde yok (yazım hatası?)")
                elif s.path is SignalPath.PIPELINE and "pipeline" not in flags:
                    out.append(f"{e.id}: {s.metric} hatta görülmedi; yol 'source' olmalı")
    return out


def check_metric_names(queries: list[str], prefixes: tuple[str, ...]) -> set[str]:
    """Kontrol sorgularındaki ürün metrik adları (son ekler atılır)."""
    pattern = r"\b((?:" + "|".join(re.escape(p) for p in prefixes) + r")[a-z0-9_]+)"
    names = {m for q in queries for m in re.findall(pattern, q)}
    return {normalize(n) for n in names}


def normalize(name: str) -> str:
    for suffix in ("_bucket", "_sum", "_total"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def scenario_exists(repo_root: Path, scenario: str | None) -> bool:
    return bool(scenario) and (repo_root / "scenarios" / str(scenario)).is_dir()


# ---------------------------------------------------------------- Markdown


_PATH_TEXT = {
    SignalPath.PIPELINE: "hatta var",
    SignalPath.SOURCE: "tanımlı, henüz görülmedi",
    SignalPath.PLATFORM: "platform metriği",
    SignalPath.LOG: "log",
    SignalPath.K8S: "Kubernetes nesnesi",
    SignalPath.NONE: "**yolda yok**",
}


def _cell(items: list[str]) -> str:
    return ", ".join(f"`{i}`" for i in items) if items else "—"


def render(cat: Catalog, repo_root: Path) -> str:
    names = {c.id: c.name for c in load_taxonomy().categories}
    order = list(cat.areas)
    entries = sorted(cat.entries, key=lambda e: (order.index(e.area), e.id))
    lines = [
        f"# {cat.title}",
        "",
        "<!-- Bu dosya catalog.yaml'dan üretilir; elle değiştirme. `make catalog` -->",
        "",
    ]
    if cat.intro:
        lines += [cat.intro.strip(), ""]
    lines += [
        "Sinyal yolları: **hatta var** = hedef ortamdaki telemetri yolundan geliyor ve canlıda "
        "görüldü; "
        "**tanımlı, henüz görülmedi** = ürünün kaynağında var, olay olmadan oluşmuyor; "
        "**platform metriği** = Kubernetes/altyapı metriği; "
        "**yolda yok** = hiçbir yoldan gelmiyor.",
        "",
        "## Özet",
        "",
        "| Arıza | Etki | Alarm | Kontrol | Senaryo | Panel | Kategori |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in entries:
        scen = e.scenario or "—"
        if e.scenario and not scenario_exists(repo_root, e.scenario):
            scen += " (planlı)"
        lines.append(
            f"| [{e.title}](#{e.id}) | {_SCOPE_TEXT[e.scope]} | {_cell(e.alerts)} | "
            f"{_cell(e.checks)} | "
            f"{scen} | {e.panel or '—'} | {names.get(e.category, e.category)} |"
        )
    current = None
    for e in entries:
        if e.area != current:
            lines += ["", f"## {cat.areas[e.area]}"]
            current = e.area
        lines += [
            "",
            f'### <a id="{e.id}"></a>{e.title}',
            "",
            f"**Belirti:** {e.symptom.strip()}",
            "",
        ]
        lines += ["| Sinyal | Yol | Not |", "| --- | --- | --- |"]
        for s in e.signals:
            lines.append(f"| `{s.label}` | {_PATH_TEXT[s.path]} | {s.note} |")
        lines.append("")
        if e.threshold:
            src = f" ({e.threshold_source})" if e.threshold_source else ""
            lines.append(f"- **Eşik:** {e.threshold}{src}")
        lines += [
            f"- **Etki:** {_SCOPE_TEXT[e.scope]}",
            f"- **Alarm:** {_cell(e.alerts)}",
            f"- **Kontrol:** {_cell(e.checks)}",
            f"- **Senaryo:** {e.scenario or '—'}",
            f"- **Panel:** {e.panel or '—'}",
            f"- **Kategori:** {names.get(e.category, e.category)}",
        ]
        if e.notes:
            lines.append(f"- **Not:** {e.notes.strip()}")
        if e.refs:
            lines.append("- **Kaynak:** " + " · ".join(e.refs))
    lines += ["", "## Eksikler", "", "Bu bölüm otomatik üretilir: bağlantısı olmayan satırlar.", ""]
    for label, missing in (
        ("Alarmı olmayan", [e for e in entries if not e.alerts]),
        ("Kontrolü olmayan", [e for e in entries if not e.checks]),
        ("Senaryosu olmayan", [e for e in entries if not e.scenario]),
        ("Paneli olmayan", [e for e in entries if not e.panel]),
    ):
        items = ", ".join(f"[{e.title}](#{e.id})" for e in missing) or "yok"
        lines.append(f"- **{label} ({len(missing)}):** {items}")
    none_signals = [(e, s) for e in entries for s in e.signals if s.path is SignalPath.NONE]
    items = ", ".join(f"`{s.label}` ([{e.title}](#{e.id}))" for e, s in none_signals) or "yok"
    lines.append(f"- **Yolda olmayan sinyaller ({len(none_signals)}):** {items}")
    if cat.known_issues:
        lines += ["", "## Bilinen sorunlar", ""]
        lines += [f"- {i.strip()}" for i in cat.known_issues]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- canlı doğrulama


# Birçok ürün metriği ilk olayda oluşur ve pod yeniden başlayınca kaybolur: "şu an var mı" değil,
# "son LOOKBACK içinde bu yoldan geldi mi" sorulur.
LOOKBACK = "7d"


async def _count(client: httpx.AsyncClient, selector: str) -> int:
    query = f"count(last_over_time({selector}[{LOOKBACK}]))"
    r = await client.get("/api/v1/query", params={"query": query})
    r.raise_for_status()
    result = r.json()["data"]["result"]
    return int(float(result[0]["value"][1])) if result else 0


def _selector(metric: str, extra: str) -> str:
    names = f"{metric}|{metric}_total|{metric}_bucket"
    inner = extra.strip().strip("{}")
    return "{" + f'__name__=~"{names}"' + (f",{inner}" if inner else "") + "}"


async def verify(cat: Catalog, prometheus_url: str) -> list[dict[str, Any]]:
    """Her metrik sinyali için beyan edilen yol ile son LOOKBACK'te görüleni karşılaştırır."""
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=prometheus_url, timeout=20) as client:
        for e in cat.entries:
            for s in e.signals:
                if not s.metric:
                    continue
                pipeline = await _count(client, _selector(s.metric, cat.pipeline_selector))
                anywhere = await _count(client, _selector(s.metric, ""))
                if s.path is SignalPath.PIPELINE:
                    status = "OK" if pipeline else f"FAIL son {LOOKBACK} hatta görülmedi"
                elif s.path is SignalPath.PLATFORM:
                    status = "OK" if anywhere else f"FAIL son {LOOKBACK} görülmedi"
                elif s.path is SignalPath.SOURCE:
                    status = (
                        "UYARI artık hatta görülüyor: yolu 'pipeline' yap" if pipeline else "OK"
                    )
                else:  # NONE
                    status = "UYARI artık görülüyor: yolu güncelle" if anywhere else "OK"
                rows.append(
                    {
                        "entry": e.id,
                        "metric": s.metric,
                        "path": str(s.path),
                        "pipeline": pipeline,
                        "anywhere": anywhere,
                        "status": status,
                    }
                )
    return rows


# ---------------------------------------------------------------- komut satırı


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sre_agent.catalog")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="catalog.yaml → catalog.md")
    r.add_argument("pack", type=Path)
    r.add_argument("--check", action="store_true", help="dosya güncel değilse hata ver")
    r.add_argument("--root", type=Path, default=Path("."))
    v = sub.add_parser("verify", help="metrikleri canlı Prometheus'ta doğrula (salt okunur)")
    v.add_argument("pack", type=Path)
    v.add_argument("--prometheus", required=True)
    args = parser.parse_args(argv)

    cat = load(args.pack)
    if args.cmd == "render":
        text = render(cat, args.root)
        target = args.pack / RENDERED_FILE
        if args.check:
            if not target.exists() or target.read_text() != text:
                print(f"{target} güncel değil: make catalog", file=sys.stderr)
                return 1
            return 0
        target.write_text(text)
        print(f"{target} yazıldı ({len(cat.entries)} arıza)")
        return 0

    rows = asyncio.run(verify(cat, args.prometheus))
    width = max(len(r["metric"]) for r in rows)
    for row in rows:
        print(f"{row['status'][:4]:<5}{row['metric']:<{width + 2}}{row['path']:<10}{row['entry']}")
        if len(row["status"]) > 4:
            print(f"     └ {row['status']}")
    failed = [r for r in rows if r["status"].startswith("FAIL")]
    print(f"{len(rows)} metrik, {len(failed)} hata")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
