"""Olay anındaki yerleşim (Prometheus geçmişinden): pod → node ve node'un o andaki durumu.

Kubernetes API yalnız bugünü bilir; node kaybından sonra pod'lar başka node'a taşınmış, node
yeniden oluşturulmuş olabilir. kube_pod_info ve kube_node_* serileri olay anını gösterir.
k8s sunucusunun `placement` aracı ve product sunucusunun `topology` aracı bunu kullanır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sre_agent.mcp.common import selector_value
from sre_agent.mcp.common import ts as fmt_ts
from sre_agent.ports import MetricsPort

UNSCHEDULED = "(node'a atanmamış)"
RECREATE_WINDOW = timedelta(hours=2)  # bu süre içinde yeniden oluşan node olayla ilişkili sayılır


@dataclass(frozen=True)
class NodeState:
    during: str  # olay penceresinde: "NotReady oldu" / "Ready" / "Ready verisi yok" / "-"
    after: str  # sonrası: "olaydan +N dk sonra yeniden oluşturuldu" / "bugün yok" / ...
    lost: bool  # olay penceresinde NotReady oldu ya da kısa süre içinde yeniden oluşturuldu


@dataclass(frozen=True)
class Placement:
    namespace: str
    at: datetime
    lookback_minutes: int
    pods: dict[str, list[str]] = field(default_factory=dict)  # node → pod'lar
    nodes: dict[str, NodeState] = field(default_factory=dict)

    @property
    def header(self) -> str:
        hours = int(RECREATE_WINDOW.total_seconds() // 3600)
        return (
            f"{self.namespace} yerleşimi @ {fmt_ts(self.at)}: olaydan önceki "
            f"{self.lookback_minutes} dk'da görülen pod'lar; 'sonra' = olaydan sonraki {hours} saat"
        )


async def placement(
    prom: MetricsPort,
    namespace: str,
    at: datetime,
    pod_regex: str = ".+",
    lookback_minutes: int = 15,
) -> Placement:
    sel = f'namespace="{namespace}",pod=~"{selector_value(pod_regex, "pod_regex")}"'
    rng = f"{lookback_minutes}m"
    rows = await prom.query(
        f"max by (pod, node) (max_over_time(kube_pod_info{{{sel}}}[{rng}]))", at
    )
    out = Placement(namespace, at, lookback_minutes)
    if not rows:
        return out

    async def by_node(promql: str, t: datetime) -> dict[str, float]:
        return {r.labels.get("node", ""): r.value for r in await prom.query(promql, t)}

    created = "max by (node) (kube_node_created)"
    ready_q = (
        'min by (node) (min_over_time(kube_node_status_condition{condition="Ready",'
        f'status="true"}}[{rng}]))'
    )
    ready = await by_node(ready_q, at)
    then = await by_node(created, at)
    soon = await by_node(created, at + RECREATE_WINDOW)
    now = await by_node(created, datetime.now(UTC))
    for r in rows:
        out.pods.setdefault(r.labels.get("node") or UNSCHEDULED, []).append(
            r.labels.get("pod", "?")
        )
    for node in out.pods:
        out.pods[node].sort()
        if node == UNSCHEDULED:
            out.nodes[node] = NodeState("-", "-", False)
            continue
        not_ready = node in ready and ready[node] < 1
        during = (
            "Ready verisi yok" if node not in ready else "NotReady oldu" if not_ready else "Ready"
        )
        t0 = then.get(node)
        recreated_soon = t0 is not None and soon.get(node, t0) > t0 + 60
        if recreated_soon:
            mins = int((soon[node] - at.timestamp()) // 60)
            after = f"olaydan {mins:+d} dk sonra yeniden oluşturuldu"
        elif node not in now:
            after = "bugün yok"
        elif t0 is not None and now[node] > t0 + 60:
            born = fmt_ts(datetime.fromtimestamp(now[node], UTC))
            after = f"olayla ilgisiz, daha sonra yeniden oluşturuldu ({born})"
        else:
            after = "aynı node"
        out.nodes[node] = NodeState(during, after, not_ready or recreated_soon)
    return out
