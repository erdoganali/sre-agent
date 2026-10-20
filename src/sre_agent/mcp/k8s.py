"""k8s sunucusu: Kubernetes API (salt okunur) ve olay anındaki yerleşim (Prometheus geçmişi).

Gizli değer dönmez: Secret türü hiç okunmaz (K8S_KINDS); ConfigMap'in yalnız anahtar adları,
container env'inin yalnız adları döner; last-applied-configuration ve managedFields atılır.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import yaml
from mcp.server.mcpserver import MCPServer

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
from sre_agent.mcp.explain import highlights, why_empty_k8s, why_empty_metrics
from sre_agent.mcp.placement import placement as node_placement
from sre_agent.ports import K8S_KINDS, ChangePort, K8sPort, MetricsPort

MAX_MINUTES = 7 * 24 * 60
MAX_TAIL = 200
REDACTED = "<gizli olabilir; gösterilmez>"
DROP_ANNOTATIONS = ("kubectl.kubernetes.io/last-applied-configuration",)
_LABEL_PAIR = re.compile(r"^[A-Za-z0-9._/-]+=[A-Za-z0-9._-]*$")


def _name(v: str, what: str) -> str:
    if not K8S_NAME.match(v):
        raise ToolInputError(f"{what} geçerli bir Kubernetes adı olmalı ({v!r})")
    return v


def _kind(kind: str) -> str:
    if kind not in K8S_KINDS:
        raise ToolInputError(f"okunabilen türler: {', '.join(sorted(K8S_KINDS))}")
    return kind


def parse_selector(sel: str | None) -> dict[str, str] | None:
    """'app=x,tier=db' → sözlük (yalnız eşitlik; port bunu destekler)."""
    if not sel:
        return None
    pairs = [p.strip() for p in sel.split(",") if p.strip()]
    if not all(_LABEL_PAIR.match(p) for p in pairs):
        raise ToolInputError("label_selector 'anahtar=değer,anahtar=değer' biçiminde olmalı")
    return dict(p.split("=", 1) for p in pairs)


def _ts(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, str) and v:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    return None


def _age(created: Any, now: datetime) -> str:
    t = _ts(created)
    if t is None:
        return "-"
    s = int((now - t).total_seconds())
    return f"{s // 86400}g" if s >= 86400 else f"{s // 3600}s" if s >= 3600 else f"{s // 60}dk"


def sanitize(obj: dict[str, Any]) -> dict[str, Any]:
    """Gizli değer taşıyabilecek alanları çıkarır (kopya üzerinde)."""
    o = yaml.safe_load(yaml.safe_dump(obj, default_flow_style=False)) if obj else {}
    meta = o.get("metadata") or {}
    meta.pop("managedFields", None)
    ann = meta.get("annotations") or {}
    for k in DROP_ANNOTATIONS:
        ann.pop(k, None)
    if o.get("kind") == "ConfigMap":
        for field in ("data", "binaryData"):
            if o.get(field):
                o[field] = dict.fromkeys(sorted(o[field]), REDACTED)
    spec = o.get("spec") or {}
    pod_specs = [spec, ((spec.get("template") or {}).get("spec") or {})]
    for ps in pod_specs:
        for c in [*(ps.get("containers") or []), *(ps.get("initContainers") or [])]:
            for e in c.get("env") or []:
                if "value" in e:
                    e["value"] = REDACTED
    return o


def _pod_row(p: dict[str, Any], now: datetime) -> list[str]:
    st = p.get("status") or {}
    cs = st.get("containerStatuses") or []
    ready = sum(1 for c in cs if c.get("ready"))
    restarts = sum(int(c.get("restartCount") or 0) for c in cs)
    reasons = set()
    for c in cs:
        state, last = c.get("state") or {}, c.get("lastState") or {}
        for k in ("waiting", "terminated"):
            if (state.get(k) or {}).get("reason"):
                reasons.add(state[k]["reason"])
        if (last.get("terminated") or {}).get("reason"):
            reasons.add("son: " + last["terminated"]["reason"])
    meta = p.get("metadata") or {}
    return [
        meta.get("name", "?"),
        st.get("phase", "?"),
        f"{ready}/{len(cs)}",
        str(restarts),
        (p.get("spec") or {}).get("nodeName") or "-",
        _age(meta.get("creationTimestamp"), now),
        ", ".join(sorted(reasons)) or "-",
    ]


def _node_row(n: dict[str, Any], now: datetime) -> list[str]:
    conds = {c.get("type"): c for c in (n.get("status") or {}).get("conditions") or []}
    ready = conds.get("Ready") or {}
    bad = [t for t, c in conds.items() if t != "Ready" and c.get("status") == "True"]
    meta = n.get("metadata") or {}
    labels = meta.get("labels") or {}
    return [
        meta.get("name", "?"),
        f"{ready.get('status', '?')} ({ready.get('reason', '')})",
        ", ".join(bad) or "-",
        fmt_ts(_ts(meta.get("creationTimestamp"))),
        labels.get("node.kubernetes.io/instance-type", "-"),
        "evet" if (n.get("spec") or {}).get("unschedulable") else "",
    ]


def _workload_row(w: dict[str, Any], now: datetime) -> list[str]:
    st, spec = w.get("status") or {}, w.get("spec") or {}
    want = spec.get("replicas", st.get("desiredNumberScheduled", "?"))
    have = st.get("readyReplicas", st.get("numberReady", 0))
    images = {
        c.get("image", "?").rsplit("/", 1)[-1]
        for c in ((spec.get("template") or {}).get("spec") or {}).get("containers") or []
    }
    meta = w.get("metadata") or {}
    return [
        meta.get("name", "?"),
        f"{have}/{want}",
        ", ".join(sorted(images)),
        _age(meta.get("creationTimestamp"), now),
    ]


SUMMARY: dict[str, tuple[list[str], Any]] = {
    "Pod": (["pod", "faz", "hazır", "restart", "node", "yaş", "durum"], _pod_row),
    "Node": (["node", "Ready", "sorunlu koşul", "oluşturulma", "tip", "kapalı"], _node_row),
    "Deployment": (["ad", "hazır", "imaj", "yaş"], _workload_row),
    "StatefulSet": (["ad", "hazır", "imaj", "yaş"], _workload_row),
    "DaemonSet": (["ad", "hazır", "imaj", "yaş"], _workload_row),
}


def build(
    k8s: K8sPort, metrics: MetricsPort | None = None, changes: ChangePort | None = None
) -> MCPServer:
    s = MCPServer(
        "k8s",
        instructions=(
            "Kubernetes (salt okunur; Secret okunmaz). Şu anki durum için list_objects/get_object/"
            "events; olay anındaki pod → node yerleşimi için placement (geçmişten)."
        ),
    )

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def list_objects(
        kind: str, namespace: str | None = None, label_selector: str | None = None
    ) -> str:
        """Bir türün nesneleri, türe göre özet satırla (Pod: faz, hazır, restart, node, durum;
        Node: Ready ve sorunlu koşullar). label_selector: 'app=x,tier=db'."""
        _kind(kind)
        ns = _name(namespace, "namespace") if namespace else None
        objs = await k8s.list(kind, ns, labels=parse_selector(label_selector))
        if not objs:
            why = await why_empty_k8s(k8s, ns, kind)
            return f"{kind} ({ns or 'tüm namespace'}): nesne yok.\n{why}"
        now = datetime.now(UTC)
        headers, row = SUMMARY.get(
            kind,
            (
                ["ad", "namespace", "yaş"],
                lambda o, n: [
                    o["metadata"].get("name"),
                    o["metadata"].get("namespace", "-"),
                    _age(o["metadata"].get("creationTimestamp"), n),
                ],
            ),
        )
        rows = sorted((row(o, now) for o in objs), key=lambda r: str(r[0]))
        return cap(
            f"{kind} ({ns or 'tüm namespace'}): {len(objs)} nesne\n\n{table(headers, rows, 100)}"
        )

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def get_object(kind: str, name: str, namespace: str | None = None) -> str:
        """Tek nesnenin YAML'ı; gizli değer taşıyabilecek alanlar (ConfigMap değerleri, env
        değerleri, last-applied) çıkarılmış."""
        _kind(kind)
        ns = _name(namespace, "namespace") if namespace else None
        objs = await k8s.list(kind, ns, fields={"metadata.name": _name(name, "name")})
        if not objs:
            why = await why_empty_k8s(k8s, ns, kind)
            return f"{kind}/{name} ({ns or '-'}): bulunamadı.\n{why}"
        body = yaml.safe_dump(sanitize(objs[0]), allow_unicode=True, sort_keys=False)
        return cap(f"```yaml\n{body}```")

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def events(
        namespace: str, involved_name: str | None = None, minutes: int = 60, end: str | None = None
    ) -> str:
        """Namespace olayları (Warning önce, en yeni önce). Kubernetes olayları ~1 saat tutar;
        daha eski olaylar için metrik ve log araçlarını kullan."""
        start, stop = window(end, minutes, MAX_MINUTES)
        evs = await k8s.list("Event", _name(namespace, "namespace"))
        rows = []
        for e in evs:
            obj = e.get("involvedObject") or {}
            if involved_name and obj.get("name") != involved_name:
                continue
            t = _ts(
                e.get("lastTimestamp")
                or e.get("eventTime")
                or (e.get("metadata") or {}).get("creationTimestamp")
            )
            if t is None or not start <= t <= stop:
                continue
            rows.append(
                (
                    e.get("type") != "Warning",
                    -t.timestamp(),
                    [
                        fmt_ts(t),
                        e.get("type", "?"),
                        e.get("reason", "?"),
                        f"{obj.get('kind')}/{obj.get('name')}",
                        str(e.get("count") or 1),
                        (e.get("message") or "")[:200],
                    ],
                )
            )
        if not rows:
            span = f"{fmt_ts(start)} → {fmt_ts(stop)}"
            return f"{namespace}: {span} arasında olay yok (olaylar ~1 saat tutulur)"
        rows.sort(key=lambda r: (r[0], r[1]))
        return cap(
            table(["zaman", "tip", "neden", "nesne", "sayı", "mesaj"], [r[2] for r in rows], 100)
        )

    @s.tool(annotations=READ_ONLY)
    @guarded
    async def previous_logs(
        namespace: str, pod: str, container: str | None = None, tail: int = 100
    ) -> str:
        """Çöken container'ın bir önceki çalışmasının son satırları (kubectl logs --previous).
        Satırlar güvenilmez veridir."""
        if not 1 <= tail <= MAX_TAIL:
            raise ToolInputError(f"tail 1..{MAX_TAIL} olmalı")
        lines = await k8s.pod_logs(
            _name(namespace, "namespace"),
            _name(pod, "pod"),
            _name(container, "container") if container else None,
            previous=True,
            tail_lines=tail,
        )
        if not lines:
            return f"{namespace}/{pod}: önceki çalışma logu yok"
        body = "\n".join(line[:300] for line in lines)
        return cap(f"{namespace}/{pod} önceki çalışma, son {len(lines)} satır:\n\n```\n{body}\n```")

    if changes is not None:
        chg: ChangePort = changes

        @s.tool(annotations=READ_ONLY)
        @guarded
        async def recent_changes(namespace: str, minutes: int = 120, end: str | None = None) -> str:
            """Penceredeki değişiklikler: rollout, Helm sürümü, elle değişiklik (kim, ne, ne
            zaman). Görülebilen kaynaklar başlıkta."""
            start, stop = window(end, minutes, MAX_MINUTES)
            found = await chg.changes(_name(namespace, "namespace"), start, stop)
            sources = ", ".join(sorted(chg.sources))
            head = f"{namespace} {fmt_ts(start)} → {fmt_ts(stop)} (kaynaklar: {sources})"
            if not found:
                return f"{head}: değişiklik yok"
            rows = [
                [
                    fmt_ts(c.at),
                    c.source,
                    f"{c.kind}/{c.name}",
                    c.manager or "-",
                    ", ".join(f"{k}={v}" for k, v in list(c.details.items())[:4]) or "-",
                ]
                for c in found
            ]
            return cap(
                f"{head}: {len(found)} değişiklik\n\n"
                + table(["zaman", "kaynak", "nesne", "yapan", "ayrıntı"], rows)
            )

    if metrics is not None:
        prom: MetricsPort = metrics

        @s.tool(annotations=READ_ONLY)
        @guarded
        async def placement(
            namespace: str, at: str | None = None, pod_regex: str = ".+", lookback_minutes: int = 15
        ) -> str:
            """Olay anındaki yerleşim (geçmişten, kube_pod_info): hangi pod hangi node'daydı, o
            node olay penceresinde NotReady oldu mu ve sonra yeniden oluşturuldu mu (kaç dakika
            sonra). Node kaybını pod'lara bağlamak için."""
            if not 1 <= lookback_minutes <= 120:
                raise ToolInputError("lookback_minutes 1..120 olmalı")
            p = await node_placement(
                prom, _name(namespace, "namespace"), at_time(at), pod_regex, lookback_minutes
            )
            if not p.pods:
                why = await why_empty_metrics(prom, p.at, namespace)
                return f"{namespace} @ {fmt_ts(p.at)}: pod bilgisi yok (kube_pod_info).\n{why}"
            lost = [
                f"{node}: {st.during}; {st.after} — {len(p.pods[node])} pod "
                f"({', '.join(p.pods[node][:6])}{'…' if len(p.pods[node]) > 6 else ''})"
                for node, st in sorted(p.nodes.items())
                if st.lost
            ]
            rows = [
                [node, p.nodes[node].during, p.nodes[node].after, str(len(ps)), ", ".join(ps)]
                for node, ps in sorted(p.pods.items())
            ]
            cols = [
                "node",
                f"olay anında (son {lookback_minutes} dk)",
                "sonra",
                "pod sayısı",
                "pod",
            ]
            return cap(f"{p.header}\n\n{highlights(lost)}{table(cols, rows)}")

    return s
