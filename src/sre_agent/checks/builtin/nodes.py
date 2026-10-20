"""Kubernetes API'den okunan kontroller: Pending pod'lar, node koşulları ve node event'leri.

Node kontrolleri alarmın namespace'indeki pod'ların çalıştığı node'lara bakar; namespace yoksa
bütün node'lara. Bakım/preemption bilgisi GCP API'sinden değil Kubernetes event'lerinden okunur.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.checks.builtin._common import namespace, prom_range, ts
from sre_agent.ports import Capability, K8sPort


class PendingPods(Check):
    """Uzun süre Pending kalan pod'lar ve zamanlama hatası (FailedScheduling) mesajları.

    Eşik 5 dk: normal zamanlama ve image çekme birkaç saniye/dakika sürer; 5 dk üstü takılmadır.
    """

    name = "pending_pods"
    capability = Capability.K8S
    categories = ("capacity_performance",)
    MIN_PENDING = timedelta(minutes=5)

    async def run(self, ctx: CheckContext) -> Evidence:
        ns = namespace(ctx)
        if not ns:
            return self.evidence(ctx, Status.NO_DATA, "alarmda namespace etiketi yok")
        k8s = ctx.k8s()
        query = f"list Pod status.phase=Pending namespace={ns}; list Event reason=FailedScheduling"
        pending = [
            p
            for p in await k8s.list("Pod", namespace=ns, fields={"status.phase": "Pending"})
            if (created := ts(p["metadata"].get("creationTimestamp")))
            and ctx.window.end - created >= self.MIN_PENDING
        ]
        if not pending:
            return self.evidence(ctx, Status.OK, f"{ns}: uzun süre Pending pod yok", query=query)
        reasons: dict[str, str] = {}
        for e in await k8s.list("Event", namespace=ns, fields={"reason": "FailedScheduling"}):
            obj = e.get("involvedObject") or {}
            if obj.get("kind") == "Pod":
                reasons[obj.get("name", "")] = e.get("message") or e.get("note") or ""
        rows = [
            {
                "pod": p["metadata"]["name"],
                "since": p["metadata"].get("creationTimestamp"),
                "scheduling": reasons.get(p["metadata"]["name"]),
            }
            for p in pending
        ]
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{ns}: {len(rows)} pod {self.MIN_PENDING.seconds // 60} dk'dan uzun Pending",
            data=rows,
            query=query,
        )


async def _scope_nodes(ctx: CheckContext, k8s: K8sPort) -> tuple[list[dict[str, Any]], str]:
    """Namespace'teki pod'ların node'ları; namespace yoksa hepsi."""
    nodes = await k8s.list("Node")
    ns = namespace(ctx)
    if not ns:
        return nodes, "tüm node'lar"
    used = {(p.get("spec") or {}).get("nodeName") for p in await k8s.list("Pod", namespace=ns)} - {
        None
    }
    return [n for n in nodes if n["metadata"]["name"] in used], f"{ns} pod'larının node'ları"


class NodeConditions(Check):
    """Node sağlığı: Ready≠True ya da Memory/Disk/PID pressure=True.

    Eşik yok: bu koşullar kubelet'in kendi değerlendirmesidir; True olması başlı başına anomalidir.
    """

    name = "node_conditions"
    capability = Capability.K8S
    categories = ("hardware_problem",)
    PRESSURE: ClassVar[tuple[str, ...]] = ("MemoryPressure", "DiskPressure", "PIDPressure")

    async def run(self, ctx: CheckContext) -> Evidence:
        k8s = ctx.k8s()
        nodes, scope = await _scope_nodes(ctx, k8s)
        query = f"list Node ({scope}) status.conditions"
        if not nodes:
            return self.evidence(ctx, Status.NO_DATA, f"{scope}: node yok", query=query)
        bad: list[dict[str, Any]] = []
        for n in nodes:
            for c in (n.get("status") or {}).get("conditions") or []:
                broken = (c.get("type") == "Ready" and c.get("status") != "True") or (
                    c.get("type") in self.PRESSURE and c.get("status") == "True"
                )
                if broken:
                    bad.append(
                        {
                            "node": n["metadata"]["name"],
                            "condition": c.get("type"),
                            "status": c.get("status"),
                            "reason": c.get("reason"),
                            "since": c.get("lastTransitionTime"),
                        }
                    )
        if not bad:
            return self.evidence(
                ctx, Status.OK, f"{scope}: {len(nodes)} node sağlıklı", query=query
            )
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{scope}: " + ", ".join(f"{b['node']} {b['condition']}={b['status']}" for b in bad),
            data=bad,
            query=query,
        )


class NodeEvents(Check):
    """Pencere içindeki node event'leri: NotReady, yeniden başlatma, baskı, preemption, bakım.

    Eşik yok: bu event'lerin pencerede görülmesi donanım/altyapı olayının kanıtıdır.
    """

    name = "node_events"
    capability = Capability.K8S
    categories = ("hardware_problem",)
    REASONS: ClassVar[frozenset[str]] = frozenset(
        {
            "NodeNotReady",
            "NodeNotSchedulable",
            "Rebooted",
            "NodeHasDiskPressure",
            "NodeHasInsufficientMemory",
            "NodeHasInsufficientPID",
            "SystemOOM",
        }
    )
    KEYWORDS: ClassVar[tuple[str, ...]] = ("preempt", "maintenance", "shutdown", "terminat")

    def _relevant(self, reason: str) -> bool:
        low = reason.lower()
        return reason in self.REASONS or any(k in low for k in self.KEYWORDS)

    async def run(self, ctx: CheckContext) -> Evidence:
        k8s = ctx.k8s()
        nodes, scope = await _scope_nodes(ctx, k8s)
        names = {n["metadata"]["name"] for n in nodes}
        query = f"list Event involvedObject.kind=Node ({scope})"
        rows: list[dict[str, Any]] = []
        for e in await k8s.list("Event", fields={"involvedObject.kind": "Node"}):
            obj = e.get("involvedObject") or {}
            reason = e.get("reason") or ""
            at = ts(e.get("lastTimestamp")) or ts(e.get("eventTime")) or ts(e.get("firstTimestamp"))
            if (
                obj.get("name") in names
                and self._relevant(reason)
                and at
                and ctx.window.start <= at <= ctx.window.end
            ):
                rows.append(
                    {
                        "node": obj.get("name"),
                        "reason": reason,
                        "at": at.isoformat(),
                        "message": e.get("message"),
                    }
                )
        if not rows:
            return self.evidence(ctx, Status.OK, f"{scope}: pencerede node olayı yok", query=query)
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{scope}: {len(rows)} node olayı ({', '.join(sorted({r['reason'] for r in rows}))})",
            data=sorted(rows, key=lambda r: r["at"]),
            query=query,
        )


class NodeRecreated(Check):
    """Pencerede node kaybı: node gitti, bir an hazır değildi ya da yeniden oluştu (Prometheus).

    GKE geri aldığı spot makineyi çoğu zaman aynı adla yeniden açar: node birkaç dakikada yine
    Ready olur ve eski nesnenin olayları kaybolur, bu yüzden node_conditions/node_events çoğu
    zaman bir şey görmez (2026-10: 10 günde ~120 yeniden oluşma; olayların çoğu raporlarda yoktu).
    Metrikler ise iz bırakır. Sıra genelde: node gider (pod'lar yeniden başlar, alarm çalar) →
    birkaç dakika sonra yeni nesne oluşur. Rapor o arada yazılırsa yalnız "gitti" görülür; bu
    yüzden üç iz birlikte aranır:
      gone       pencerede vardı, şimdi yok (kube_node_info)
      not_ready  pencerede bir an Ready değildi (kube_node_status_condition)
      recreated  oluşturulma zamanı pencerede değişti ya da pencerede oluştu (kube_node_created)

    Kapsam: alarmda namespace varsa yalnız o namespace'in pod'larının pencerede bulunduğu
    node'lar (kube_pod_info geçmişi); yoksa kümenin bütün node'ları. Kapsamsız hâlde başka bir
    namespace'in alarmı (ör. aynı anda koşan bir lab senaryosu) node kaybına bağlanıyordu.
    Pencerede yeni ad alan node otomatik ölçekleme de olabilir; özet bunu "yeni node" diye yazar.
    """

    name = "node_recreated"
    capability = Capability.METRICS
    categories = ("hardware_problem",)
    queries: ClassVar[dict[str, str]] = {
        "gone": (
            "max by (node) (max_over_time(kube_node_info[{range}]))"
            " unless max by (node) (kube_node_info)"
        ),
        "not_ready": (
            'min_over_time(max by (node) (kube_node_status_condition{{condition="Ready",'
            'status="true"}})[{range}:1m]) == 0'
        ),
        "recreated": "changes(max by (node) (kube_node_created)[{range}:1m]) > 0",
        "created": "max by (node) (kube_node_created) > {start}",
    }
    LABELS: ClassVar[dict[str, str]] = {
        "gone": "gitti",
        "not_ready": "hazır değildi",
        "recreated": "yeniden oluştu",
        "created": "yeni node",
    }

    SCOPE = (
        " and on (node) max by (node) "
        '(max_over_time(kube_pod_info{{namespace="{ns}",node!=""}}[{range}]))'
    )

    async def run(self, ctx: CheckContext) -> Evidence:
        m = ctx.metrics()
        ns = namespace(ctx)
        args = {"range": prom_range(ctx), "start": int(ctx.window.start.timestamp()), "ns": ns}
        scope = self.SCOPE.format(**args) if ns else ""
        rendered = {k: f"({q.format(**args)}){scope}" for k, q in self.q.items()}
        query = "\n".join(rendered.values())
        found: dict[str, dict[str, float]] = {}
        for kind, q in rendered.items():
            for smp in await m.query(q, ctx.window.end):
                found.setdefault(smp.labels.get("node", "?"), {})[kind] = smp.value
        if not found:
            total = await m.query("count(kube_node_info)", ctx.window.end)
            if not total:
                return self.evidence(ctx, Status.NO_DATA, "node metriği yok", query=query)
            where = f"{ns} pod'larının node'larında" if ns else "pencerede"
            return self.evidence(ctx, Status.OK, f"{where} node kaybı yok", query=query)
        rows: list[dict[str, Any]] = []
        parts: list[str] = []
        for node, kinds in sorted(found.items()):
            created_ts = kinds.get("created")  # kaybın kimliği: node + yeni oluşturulma anı
            if "created" in kinds and "recreated" in kinds:
                del kinds["created"]  # aynı adla yeniden oluştu: "yeni node" değil
            events = [self.LABELS[k] for k in self.q if k in kinds]
            changes = int(kinds.get("recreated", 0))
            parts.append(
                f"{node} {'/'.join(events)}" + (f" ({changes} kez)" if changes > 1 else "")
            )
            rows.append(
                {
                    "node": node,
                    "events": events,
                    "changes": changes,
                    "created_at": datetime.fromtimestamp(created_ts, UTC).isoformat()
                    if created_ts
                    else None,
                }
            )
        return self.evidence(
            ctx, Status.ANOMALY, f"pencerede node kaybı: {', '.join(parts)}", data=rows, query=query
        )
