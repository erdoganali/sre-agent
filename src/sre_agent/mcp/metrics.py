"""metrics sunucusu: Prometheus."""

from __future__ import annotations

import re
from collections.abc import Sequence

from mcp.server.mcpserver import MCPServer

from sre_agent.mcp.common import (
    K8S_NAME,
    READ_ONLY,
    ToolInputError,
    at_time,
    cap,
    guarded,
    labels_text,
    num,
    selector_value,
    table,
    window,
)
from sre_agent.mcp.common import ts as fmt_ts
from sre_agent.mcp.explain import highlights, why_empty_metrics
from sre_agent.ports import MetricsPort, Series

MAX_MINUTES = 7 * 24 * 60  # Prometheus saklama süresi içinde kalsın diye bir hafta
_NAME = re.compile(r"^[a-zA-Z0-9_:.*+?|()\[\]^$-]+$")
_LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


RESTART_MIN = 1
THROTTLE_HIGH = 0.25
MEMORY_HIGH = 0.90
BAD_REASONS = ("OOMKilled", "Error", "ContainerCannotRun")


def signal_highlights(raw: dict[str, dict[str, float]], reasons: dict[str, list[str]]) -> list[str]:
    """Eşik aşan pod'lar: restart, son sonlanma nedeni, throttling, limite yakın bellek."""
    out = []
    for pod in sorted(set(raw) | set(reasons)):
        v = raw.get(pod, {})
        parts = []
        if v.get("restart", 0) >= RESTART_MIN:
            parts.append(f"{num(v['restart'])} restart")
        bad = [r for r in reasons.get(pod, []) if r in BAD_REASONS]
        if bad:
            parts.append("son sonlanma: " + ", ".join(sorted(bad)))
        if v.get("throttle", 0) >= THROTTLE_HIGH:
            parts.append(f"CPU throttling {v['throttle']:.0%}")
        lim = v.get("bellek_limit") or 0
        if lim and v.get("bellek", 0) / lim >= MEMORY_HIGH:
            parts.append(f"bellek limitin {v['bellek'] / lim:.0%}'i")
        if parts:
            out.append(f"{pod}: " + "; ".join(parts))
    return out


def _step(minutes: int) -> int:
    """~120 noktalı seri: özet için yeterli, sorgu hafif."""
    return max(15, minutes * 60 // 120)


def summarize(series: Sequence[Series]) -> list[list[str]]:
    rows = []
    for s in series:
        vals = [v for _, v in s.points if v == v]
        if not vals:
            continue
        peak_t, peak = max(s.points, key=lambda p: p[1] if p[1] == p[1] else float("-inf"))
        rows.append(
            [
                labels_text(s.labels),
                num(min(vals)),
                num(sum(vals) / len(vals)),
                num(peak),
                fmt_ts(peak_t),
                num(vals[0]),
                num(vals[-1]),
            ]
        )
    return rows


def build(port: MetricsPort) -> MCPServer:
    s = MCPServer(
        "metrics",
        instructions="Prometheus metrikleri (salt okunur). Zaman ISO 8601 UTC; pencere dakika.",
    )

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def query(promql: str, at: str | None = None) -> str:
        """Anlık PromQL sorgusu (`at` anında; boşsa şimdi). Değere göre azalan, en çok 50 satır."""
        when = at_time(at)
        rows = sorted(await port.query(promql, when), key=lambda r: -r.value)
        if not rows:
            return f"`{promql}` @ {fmt_ts(when)}: sonuç yok.\n{await why_empty_metrics(port, when)}"
        body = table(["etiketler", "değer"], [[labels_text(r.labels), num(r.value)] for r in rows])
        return cap(f"`{promql}` @ {fmt_ts(when)} — {len(rows)} seri\n\n{body}")

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def range_summary(promql: str, minutes: int = 60, end: str | None = None) -> str:
        """PromQL aralık sorgusu, seri başına özet (ham nokta yok): min, ort, tepe ve anı, ilk,
        son."""
        start, stop = window(end, minutes, MAX_MINUTES)
        series = await port.query_range(promql, start, stop, _step(minutes))
        rows = summarize(series)
        if not rows:
            why = await why_empty_metrics(port, stop)
            return f"`{promql}` {fmt_ts(start)} → {fmt_ts(stop)}: sonuç yok.\n{why}"
        rows.sort(key=lambda r: r[0])
        body = table(["etiketler", "min", "ort", "tepe", "tepe anı", "ilk", "son"], rows)
        return cap(f"`{promql}` {fmt_ts(start)} → {fmt_ts(stop)} — {len(rows)} seri\n\n{body}")

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def discover(name_regex: str, label: str | None = None) -> str:
        """Metrik keşfi: adı regex'e uyan metrikler ve seri sayıları; `label` verilirse o
        etiketin değerleri (ör. name_regex='kube_pod_.*', label='namespace')."""
        if not _NAME.match(name_regex):
            raise ToolInputError("name_regex yalnız metrik adı karakterleri ve regex içerebilir")
        if label is not None and not _LABEL.match(label):
            raise ToolInputError("label geçerli bir Prometheus etiket adı olmalı")
        by = f"__name__, {label}" if label else "__name__"
        rows = await port.query(f'count by ({by}) ({{__name__=~"{name_regex}"}})', at_time(None))
        if not rows:
            return f"`{name_regex}`: metrik yok"
        out = sorted(rows, key=lambda r: (r.labels.get("__name__", ""), -r.value))
        cols = ["metrik", label, "seri"] if label else ["metrik", "seri"]
        body = table(
            [c for c in cols if c],
            [
                [r.labels.get("__name__", "")]
                + ([r.labels.get(label, "")] if label else [])
                + [num(r.value)]
                for r in out
            ],
            max_rows=100,
        )
        return cap(body)

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def workload_signals(
        namespace: str, pod_regex: str = ".+", minutes: int = 60, end: str | None = None
    ) -> str:
        """Composite: bir namespace'in pod'ları için restart, son sonlanma nedeni (OOMKilled…),
        CPU (çekirdek) ve limiti, CPU throttling oranı, bellek ve limiti — tek çağrıda."""
        if not K8S_NAME.match(namespace):
            raise ToolInputError("namespace geçerli bir Kubernetes adı olmalı")
        start, stop = window(end, minutes, MAX_MINUTES)
        sel = f'namespace="{namespace}",pod=~"{selector_value(pod_regex, "pod_regex")}"'
        ctr = sel + ',container!=""'
        rng = f"{minutes}m"

        def by_pod(expr: str) -> str:
            return f"sum by (pod) ({expr})"

        queries = {
            "restart": by_pod(
                f"increase(kube_pod_container_status_restarts_total{{{sel}}}[{rng}])"
            ),
            "neden": f"max by (pod, reason) "
            f"(kube_pod_container_status_last_terminated_reason{{{sel}}})",
            "cpu": by_pod(f"rate(container_cpu_usage_seconds_total{{{ctr}}}[5m])"),
            "cpu_limit": by_pod(f'kube_pod_container_resource_limits{{{sel},resource="cpu"}}'),
            "throttle": by_pod(
                f"increase(container_cpu_cfs_throttled_periods_total{{{ctr}}}[{rng}])"
            )
            + " / "
            + by_pod(f"increase(container_cpu_cfs_periods_total{{{ctr}}}[{rng}])"),
            "bellek": by_pod(f"max_over_time(container_memory_working_set_bytes{{{ctr}}}[{rng}])"),
            "bellek_limit": by_pod(
                f'kube_pod_container_resource_limits{{{sel},resource="memory"}}'
            ),
        }
        raw: dict[str, dict[str, float]] = {}
        reasons: dict[str, list[str]] = {}
        for key, q in queries.items():
            for r in await port.query(q, stop):
                pod = r.labels.get("pod", "?")
                if key == "neden":
                    if r.value > 0:
                        reasons.setdefault(pod, []).append(r.labels.get("reason", "?"))
                    continue
                raw.setdefault(pod, {})[key] = r.value
        pods = sorted(set(raw) | set(reasons))
        if not pods:
            why = await why_empty_metrics(port, stop, namespace)
            span = f"{fmt_ts(start)} → {fmt_ts(stop)}"
            return f"{namespace} ({pod_regex}) {span}: pod metriği yok.\n{why}"
        cols = ["restart", "neden", "cpu", "cpu_limit", "throttle", "bellek", "bellek_limit"]

        def cell(pod: str, c: str) -> str:
            if c == "neden":
                return ", ".join(sorted(reasons.get(pod, []))) or "-"
            v = raw.get(pod, {}).get(c)
            if v is None:
                return "-"
            return f"{v:.0%}" if c == "throttle" else num(v)

        rows = [[pod] + [cell(pod, c) for c in cols] for pod in pods]
        head = (
            f"{namespace} pod'ları, {fmt_ts(start)} → {fmt_ts(stop)} "
            "(restart ve throttle pencere boyunca; cpu/bellek: son 5 dk / pencere tepesi)"
        )
        notes = signal_highlights(raw, reasons)
        return cap(f"{head}\n\n{highlights(notes)}{table(['pod', *cols], rows)}")

    return s
