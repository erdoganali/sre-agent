"""product sunucusu: domain paketlerinin kontrolleri ve topology'si (T3.3, ADR-030).

Çekirdek ürün adı bilmez: kontroller ve topology yüklenen paketlerden gelir. `topology` aracı
paketin statik haritasını (bileşenler, bağımlılıklar) olay anındaki yerleşimle (placement) ve
ürünün canlı kaynağından yalnız izinli alanlarla birleştirir.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from mcp.server.mcpserver import MCPServer

from sre_agent.checks import Check, CheckContext, Window, run_checks
from sre_agent.knowledge import KINDS, KnowledgeStore
from sre_agent.mcp.common import (
    K8S_NAME,
    READ_ONLY,
    ToolInputError,
    at_time,
    cap,
    guarded,
    table,
    window,
)
from sre_agent.mcp.common import ts as fmt_ts
from sre_agent.mcp.explain import highlights, why_empty_metrics
from sre_agent.mcp.placement import UNSCHEDULED, placement
from sre_agent.ports import Capability, K8sPort, MetricsPort
from sre_agent.topology import Topology, pick

MAX_MINUTES = 24 * 60
DATA_MAX = 3000


def _ns(v: str) -> str:
    if not K8S_NAME.match(v):
        raise ToolInputError(f"namespace geçerli bir Kubernetes adı olmalı ({v!r})")
    return v


def render_topology(
    topo: Topology,
    pods: Mapping[str, list[str]],
    lost: Mapping[str, str],
    live: list[dict[str, Any]],
) -> str:
    """pods: node → pod'lar; lost: kaybedilen node → durum metni; live: izinli alanlar."""
    on: dict[str, list[tuple[str, str]]] = {}  # bileşen → [(pod, node)]
    for node, ps in pods.items():
        for p in ps:
            comp = topo.component_of(p)
            if comp:
                on.setdefault(comp, []).append((p, node))
    rows = []
    for name, c in topo.components.items():
        placed = on.get(name, [])
        nodes = sorted({n for _, n in placed})
        hit = [n for n in nodes if n in lost]
        rows.append(
            [
                name,
                str(len(placed)),
                ", ".join(nodes) or "-",
                "evet: " + ", ".join(hit) if hit else "",
                ", ".join(c.depends) or "-",
            ]
        )
    notes = []
    for name in topo.components:
        members = {pod for pod, _ in on.get(name, [])}
        down = {pod for pod, node in on.get(name, []) if node in lost}
        if down:
            share = " (yarıdan fazlası)" if len(down) * 2 > len(members) else ""
            notes.append(
                f"{name}: {len(down)}/{len(members)} pod kaybedilen node'larda{share}; "
                f"bağlı bileşenler: {', '.join(topo.dependents(name)) or '-'}"
            )
    parts = [
        highlights(notes).rstrip("\n"),
        table(["bileşen", "pod", "node", "kaybedilen node'da", "bağlı olduğu"], rows),
    ]
    if lost:
        lines = []
        for node in sorted(lost):
            here = sorted((topo.component_of(p) or "?", p) for p in pods.get(node, []))
            comps = sorted({c for c, _ in here if c != "?"})
            lines.append(
                f"- **{node}** ({lost[node]}): "
                + (", ".join(f"{c} ({p})" for c, p in here) or "pod yok")
            )
            for comp in comps:
                dep = topo.dependents(comp)
                if dep:
                    lines.append(f"  - bağlı bileşenler ({comp}): {', '.join(dep)}")
        parts.append("Kaybedilen node'lar ve etkisi:\n\n" + "\n".join(lines))
    else:
        parts.append("Olay penceresinde kaybedilen node yok.")
    if topo.externals:
        parts.append(
            "Dış bağımlılıklar: "
            + "; ".join(
                f"{n} ({e.role}) ← {', '.join(topo.dependents(n))}"
                for n, e in topo.externals.items()
            )
        )
    if live:
        blocks = [
            "\n".join(
                f"- {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in sorted(item.items())
            )
            for item in live
        ]
        parts.append("Canlı kaynak (bugünkü durum, izinli alanlar):\n\n" + "\n\n".join(blocks))
    return "\n\n".join(parts)


def build(
    checks: Mapping[str, Check],
    ports: Mapping[Capability, Any],
    topologies: Mapping[str, Topology],
    kb: KnowledgeStore | None = None,
) -> MCPServer:
    """checks: paket kontrolleri (ad → örnek); topologies: paket adı → topology;
    kb: bilgi tabanı (RAG), varsa `knowledge_search` aracı eklenir."""
    s = MCPServer(
        "product",
        instructions=(
            "Ürün paketinin kontrolleri (run_check) ve topology'si: bileşenler, bağımlılıklar, "
            "olay anındaki yerleşim. Kaybedilen node'u bileşenlere bağlamak için önce topology."
        ),
    )

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def list_checks() -> str:
        """Paket kontrolleri: ad, yetenek, işaret ettiği kategoriler, ne baktığı."""
        rows = [
            [
                n,
                str(c.capability),
                ", ".join(c.categories),
                ((type(c).__doc__ or "").strip().splitlines() or [""])[0],
            ]
            for n, c in sorted(checks.items())
        ]
        return table(["kontrol", "yetenek", "kategoriler", "açıklama"], rows, 100)

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def run_check(
        name: str, namespace: str, minutes: int = 30, end: str | None = None
    ) -> str:
        """Bir paket kontrolünü namespace için verilen pencerede koşturur (list_checks'teki adlar);
        sonuç: durum, özet, sorgu, veri."""
        check = checks.get(name)
        if check is None:
            raise ToolInputError(f"bilinmeyen kontrol {name!r}; list_checks'e bak")
        start, stop = window(end, minutes, MAX_MINUTES)
        ctx = CheckContext(
            labels={"namespace": _ns(namespace)},
            window=Window(start=start, end=stop),
            ports=ports,
        )
        (ev,) = await run_checks([check], ctx)
        lines = [
            f"{ev.check} [{ev.status}] {fmt_ts(start)} → {fmt_ts(stop)}: {ev.summary}",
            f"kategoriler: {', '.join(ev.categories)}",
        ]
        if ev.query:
            lines.append(f"sorgu: `{ev.query}`")
        if ev.error:
            lines.append(f"hata: {ev.error}")
        if ev.data is not None:
            data = json.dumps(ev.data, ensure_ascii=False, default=str)
            lines.append("veri: " + (data if len(data) <= DATA_MAX else data[:DATA_MAX] + " …"))
        return cap("\n".join(lines))

    if kb is not None:
        base: KnowledgeStore = kb

        @s.tool(annotations=READ_ONLY)
        @guarded
        async def knowledge_search(query: str, limit: int = 3, kind: str | None = None) -> str:
            """Bilgi tabanında arama (BM25): bilinen arızalar (belirti, sinyal, eşik, alarm,
            kontrol), uzman rehberleri ve kategori tanımları. Sorguya belirti, hata mesajı,
            alarm ya da kontrol adı yaz. kind: failure | guide | category."""
            if not 1 <= limit <= 10:
                raise ToolInputError("limit 1..10 olmalı")
            if kind is not None and kind not in KINDS:
                raise ToolInputError(f"kind şunlardan biri olmalı: {', '.join(KINDS)}")
            if not query.strip():
                raise ToolInputError("query boş olamaz")
            hits = await base.search(query[:500], limit, kind)
            if not hits:
                return "Eşleşen belge yok; farklı kelimelerle (belirti, metrik, alarm adı) dene."
            return cap(
                "\n\n".join(
                    f"### {h.doc.title} ({h.doc.kind}, kategori: {h.doc.category or '-'}, "
                    f"skor {h.score:.1f})\n{h.doc.text[:1500]}"
                    for h in hits
                )
            )

    prom: MetricsPort | None = ports.get(Capability.METRICS)
    k8s: K8sPort | None = ports.get(Capability.K8S)
    if topologies and prom is not None:
        metrics: MetricsPort = prom

        @s.tool(annotations=READ_ONLY)
        @guarded
        async def topology(
            namespace: str, at: str | None = None, lookback_minutes: int = 15
        ) -> str:
            """Ürün topology'si olay anında: hangi bileşen hangi node'daydı, kaybedilen node'da
            hangi bileşenler vardı, onlara bağlı bileşenler ve dış bağımlılıklar; ürünün canlı
            kaynağından izinli alanlar (bucket, etcd adresleri…). Parola ve config dönmez."""
            if not 1 <= lookback_minutes <= 120:
                raise ToolInputError("lookback_minutes 1..120 olmalı")
            ns = _ns(namespace)
            when = at_time(at)
            p = await placement(metrics, ns, when, ".+", lookback_minutes)
            lost = {
                n: f"{st.during}; {st.after}"
                for n, st in p.nodes.items()
                if st.lost and n != UNSCHEDULED
            }
            out = [p.header]
            for pack, topo in sorted(topologies.items()):
                matched = any(topo.component_of(pod) for ps in p.pods.values() for pod in ps)
                live: list[dict[str, Any]] = []
                if topo.live and k8s is not None:
                    items = await k8s.list_custom(topo.live.group, topo.live.kind, ns)
                    live = [pick(i, topo.live.fields) for i in items]
                if not matched and not live:
                    continue
                out.append(f"## {pack}\n\n" + render_topology(topo, p.pods, lost, live))
            if len(out) == 1:
                why = await why_empty_metrics(metrics, when, ns) if not p.pods else ""
                return f"{p.header}\n\nBu namespace'te paket bileşeni bulunamadı.\n{why}".rstrip()
            return cap("\n\n".join(out))

    return s
