"""VectorDB / Milvus kontrolleri.

Sorgular `packs/vectordb/queries.yaml` dosyasında: kontroller ve "VectorDB / Milvus" dashboard'u
aynı PromQL'i oradan okur (T1.13). Milvus metrikleri telemetri hattından gelir: VectorDB gateway →
Kafka → telemetry-consumer (`job="milvus-metrics"`, namespace etiketi `k8s_namespace_name`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import yaml

from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.checks.builtin._common import namespace, pct, prom_range
from sre_agent.checks.builtin.capacity import Signal
from sre_agent.ports import Capability, MetricsPort

QUERIES_FILE = Path(__file__).parents[1] / "queries.yaml"
_Q: dict[str, Any] = yaml.safe_load(QUERIES_FILE.read_text())
CHECK_QUERIES: dict[str, dict[str, str]] = _Q["checks"]

# golden_signals_baseline'a eklenen Milvus sinyalleri (istek hızı ve arama gecikmesi).
MILVUS_SIGNALS = (
    Signal("milvus_requests_per_s", _Q["signals"]["milvus_requests_per_s"], 1.0, "istek/sn"),
    Signal("milvus_search_p99_ms", _Q["signals"]["milvus_search_p99_ms"], 100.0, "ms"),
)


class _MilvusCheck(Check):
    _base = True
    capability = Capability.METRICS
    categories = ("database",)

    def render(self, key: str, ctx: CheckContext, ns: str) -> str:
        return self.q[key].format(ns=ns, range=prom_range(ctx))

    async def one(self, m: MetricsPort, query: str, ctx: CheckContext) -> float | None:
        result = await m.query(query, ctx.window.end)
        return result[0].value if result else None

    async def run(self, ctx: CheckContext) -> Evidence:
        ns = namespace(ctx)
        if not ns:
            return self.evidence(ctx, Status.NO_DATA, "alarmda namespace etiketi yok")
        return await self.check(ctx, ctx.metrics(), ns)

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        raise NotImplementedError


class MilvusQueryNodeMemoryQuota(_MilvusCheck):
    """querynode belleği ile Milvus'un yazma kesme eşiği (qn_mem_high_water_level).

    Milvus querynode belleği bu orana (varsayılan 0.95) ulaşınca yazmaları reddeder; 0.85'te
    yavaşlatmaya başlar. Anomali: bellek/limit > eşik - MARGIN, ya da pencerede
    force-deny-writing arttı. MARGIN=0.15: varsayılan eşikte 0.80; MilvusQueryNodeMemoryHigh
    alarmıyla aynı düzey (alarm çalınca rapor da görsün), yavaşlatma eşiğinden 5 puan önce.
    """

    name = "milvus_querynode_memory_quota"
    MARGIN = 0.15
    DEFAULT_HWL = 0.95
    queries: ClassVar[dict[str, str]] = CHECK_QUERIES["milvus_querynode_memory_quota"]

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = "\n".join(self.render(k, ctx, ns) for k in self.queries)
        ratios = await m.query(self.render("ratio", ctx, ns), ctx.window.end)
        if not ratios:
            return self.evidence(
                ctx, Status.NO_DATA, f"{ns}: querynode bellek limiti yok", query=query
            )
        hwl = await self.one(m, self.render("hwl", ctx, ns), ctx) or self.DEFAULT_HWL
        denied = await self.one(m, self.render("deny", ctx, ns), ctx) or 0.0
        rows: list[dict[str, Any]] = [
            {
                "pod": s.labels.get("pod"),
                "ratio": round(s.value, 3),
                "near_quota": s.value > hwl - self.MARGIN,
            }
            for s in ratios
        ]
        data = {"high_water_level": hwl, "deny_writing_increase": denied, "querynodes": rows}
        near = [r for r in rows if r["near_quota"]]
        if denied > 0:
            return self.evidence(
                ctx,
                Status.ANOMALY,
                f"{ns}: Milvus yazmaları reddetti (force-deny-writing +{denied:.0f})",
                data=data,
                query=query,
            )
        if near:
            top = max(near, key=lambda r: float(r["ratio"]))
            return self.evidence(
                ctx,
                Status.ANOMALY,
                f"{ns}: querynode {top['pod']} belleği {pct(top['ratio'])}; "
                f"yazma kesme eşiği {pct(hwl)}",
                data=data,
                query=query,
            )
        top = max(rows, key=lambda r: float(r["ratio"]))
        return self.evidence(
            ctx,
            Status.OK,
            f"{ns}: querynode belleği en fazla {pct(top['ratio'])} (eşik {pct(hwl)})",
            data=data,
            query=query,
        )


class MilvusHealth(Check):
    """Milvus CR'ının (milvus-operator) durumu ve koşulları: MilvusReady, EtcdReady, StorageReady…

    Eşik yok: operatörün kendi sağlık değerlendirmesi; Healthy dışı durum ya da False koşul anomali.
    RBAC: milvus.io/milvuses okuma (packs/vectordb/rbac-values.yaml).
    """

    name = "milvus_health"
    capability = Capability.K8S
    categories = ("database", "vendor_external")

    async def run(self, ctx: CheckContext) -> Evidence:
        ns = namespace(ctx)
        if not ns:
            return self.evidence(ctx, Status.NO_DATA, "alarmda namespace etiketi yok")
        query = f"list milvus.io/Milvus namespace={ns}"
        items = await ctx.k8s().list_custom("milvus.io", "Milvus", ns)
        if not items:
            return self.evidence(ctx, Status.NO_DATA, f"{ns}: Milvus kaynağı yok", query=query)
        bad: list[dict[str, Any]] = []
        for mv in items:
            status = mv.get("status") or {}
            name = mv["metadata"]["name"]
            if status.get("status") != "Healthy":
                bad.append({"milvus": name, "status": status.get("status")})
            for c in status.get("conditions") or []:
                if c.get("status") != "True":
                    bad.append(
                        {
                            "milvus": name,
                            "condition": c.get("type"),
                            "reason": c.get("reason"),
                            "message": c.get("message"),
                        }
                    )
        if not bad:
            return self.evidence(ctx, Status.OK, f"{ns}: {len(items)} Milvus Healthy", query=query)
        desc = ", ".join(
            f"{b['milvus']} {b.get('condition') or 'status'}={b.get('reason') or b.get('status')}"
            for b in bad
        )
        return self.evidence(ctx, Status.ANOMALY, f"{ns}: {desc}", data=bad, query=query)


class MilvusCoordinators(_MilvusCheck):
    """Koordinatörün gördüğü node'lar ve querynode heartbeat'leri.

    Anomali: querynode ya da datanode sayısı 0 (arama/yazma yapılamaz) veya bir querynode'un
    son heartbeat'i HEARTBEAT_S'den eski. 60 sn: Milvus oturum TTL'i varsayılan olarak bu mertebede.
    """

    name = "milvus_coordinators"
    HEARTBEAT_S = 60
    queries: ClassVar[dict[str, str]] = CHECK_QUERIES["milvus_coordinators"]

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = "\n".join(self.render(k, ctx, ns) for k in self.queries)
        qn = await self.one(m, self.render("querynodes", ctx, ns), ctx)
        dn = await self.one(m, self.render("datanodes", ctx, ns), ctx)
        if qn is None and dn is None:
            return self.evidence(ctx, Status.NO_DATA, f"{ns}: koordinatör metriği yok", query=query)
        stale = [
            {"node_id": s.labels.get("node_id"), "seconds": round(s.value)}
            for s in await m.query(self.render("stale", ctx, ns), ctx.window.end)
            if s.value > self.HEARTBEAT_S
        ]
        data = {"querynodes": qn, "datanodes": dn, "stale_heartbeats": stale}
        problems = []
        if qn == 0:
            problems.append("querynode yok")
        if dn == 0:
            problems.append("datanode yok")
        if stale:
            problems.append(f"{len(stale)} querynode heartbeat'i {self.HEARTBEAT_S} sn'den eski")
        if problems:
            return self.evidence(
                ctx, Status.ANOMALY, f"{ns}: {'; '.join(problems)}", data=data, query=query
            )
        return self.evidence(
            ctx,
            Status.OK,
            f"{ns}: {qn:g} querynode, {dn:g} datanode, heartbeat'ler güncel",
            data=data,
            query=query,
        )


class MilvusEtcd(_MilvusCheck):
    """Milvus'un etcd'si: pod hazır değil / yeniden başladı, meta işlemleri hata veriyor.

    Anomali: hazır olmayan etcd pod'u, pencerede etcd restart'ı ya da meta işlem hatası (> 0).
    Meta işlem hatası Milvus'un etcd'ye yazıp okuyamadığının doğrudan kanıtıdır.
    """

    name = "milvus_etcd"
    queries: ClassVar[dict[str, str]] = CHECK_QUERIES["milvus_etcd"]

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = "\n".join(self.render(k, ctx, ns) for k in self.queries)
        if not await m.query(self.render("exists", ctx, ns), ctx.window.end):
            return self.evidence(ctx, Status.NO_DATA, f"{ns}: etcd pod'u yok", query=query)
        not_ready = [
            s.labels.get("pod")
            for s in await m.query(self.render("not_ready", ctx, ns), ctx.window.end)
        ]
        restarts = {
            str(s.labels.get("pod")): s.value
            for s in await m.query(self.render("restarts", ctx, ns), ctx.window.end)
        }
        meta_fail = await self.one(m, self.render("meta_fail", ctx, ns), ctx) or 0.0
        data = {"not_ready": not_ready, "restarts": restarts, "meta_op_failures": meta_fail}
        problems = []
        if not_ready:
            problems.append(f"hazır olmayan: {', '.join(map(str, not_ready))}")
        if restarts:
            problems.append(f"yeniden başlayan: {', '.join(restarts)}")
        if meta_fail > 0:
            problems.append(f"{meta_fail:.0f} meta işlem hatası")
        if problems:
            return self.evidence(
                ctx, Status.ANOMALY, f"{ns} etcd: {'; '.join(problems)}", data=data, query=query
            )
        return self.evidence(
            ctx, Status.OK, f"{ns}: etcd sağlıklı, meta işlem hatası yok", data=data, query=query
        )


class MilvusObjectStorage(_MilvusCheck):
    """Milvus'un object storage'ı (S3 API): hata oranı ve p99 gecikme.

    Anomali: hata oranı > %1 (en az MIN_FAILS hata) ya da p99 > P99_MS. %1: nesne depolarında
    geçici hatalar yeniden denenir, kalıcı yüzdelik hata kök nedendir. P99_MS=2000: GCS isteklerinin
    tipik p99'u yüzlerce ms; 2 sn sorgu gecikmesine yansır.
    """

    name = "milvus_object_storage"
    categories = ("vendor_external",)
    FAIL_RATIO = 0.01
    MIN_FAILS = 5
    P99_MS = 2000.0
    queries: ClassVar[dict[str, str]] = CHECK_QUERIES["milvus_object_storage"]

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = "\n".join(self.render(k, ctx, ns) for k in self.queries)
        total = await self.one(m, self.render("total", ctx, ns), ctx)
        if total is None:
            return self.evidence(ctx, Status.NO_DATA, f"{ns}: storage metriği yok", query=query)
        fails = await self.one(m, self.render("fails", ctx, ns), ctx) or 0.0
        p99 = await self.one(m, self.render("p99", ctx, ns), ctx)
        ratio = fails / total if total else 0.0
        data = {"ops": total, "fails": fails, "fail_ratio": round(ratio, 4), "p99_ms": p99}
        problems = []
        if fails >= self.MIN_FAILS and ratio > self.FAIL_RATIO:
            problems.append(f"hata oranı {pct(ratio)} ({fails:.0f}/{total:.0f})")
        if p99 is not None and p99 == p99 and p99 > self.P99_MS:  # NaN değil
            problems.append(f"p99 {p99:.0f} ms")
        if problems:
            return self.evidence(
                ctx,
                Status.ANOMALY,
                f"{ns} object storage: {'; '.join(problems)}",
                data=data,
                query=query,
            )
        return self.evidence(
            ctx,
            Status.OK,
            f"{ns}: object storage {total:.0f} işlem, {fails:.0f} hata",
            data=data,
            query=query,
        )


class MilvusSegmentBacklog(_MilvusCheck):
    """Compaction ve segment birikimi (growing/flushing segment sayısı, bekleyen compaction).

    Eşikler: GROWING=200 segment, PENDING_COMPACTION=50 görev; bunların üstü flush/compaction'ın
    yazma hızına yetişemediğini gösterir. Bu metrikler şu an Kafka yolunda yok (tenant metrik
    ajanı süzüyor olabilir) → no_data; ajan izin verdiğinde çalışır (T1.8).
    """

    name = "milvus_segment_backlog"
    GROWING = 200
    PENDING_COMPACTION = 50
    queries: ClassVar[dict[str, str]] = CHECK_QUERIES["milvus_segment_backlog"]

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = "\n".join(self.render(k, ctx, ns) for k in self.queries)
        growing = await self.one(m, self.render("growing", ctx, ns), ctx)
        compaction = await self.one(m, self.render("compaction", ctx, ns), ctx)
        if growing is None and compaction is None:
            return self.evidence(
                ctx, Status.NO_DATA, f"{ns}: segment/compaction metriği yok", query=query
            )
        data = {"growing_or_flushing": growing, "pending_compaction": compaction}
        problems = []
        if growing is not None and growing > self.GROWING:
            problems.append(f"{growing:.0f} growing/flushing segment")
        if compaction is not None and compaction > self.PENDING_COMPACTION:
            problems.append(f"{compaction:.0f} bekleyen compaction")
        if problems:
            return self.evidence(
                ctx, Status.ANOMALY, f"{ns}: {'; '.join(problems)}", data=data, query=query
            )
        return self.evidence(
            ctx, Status.OK, f"{ns}: segment/compaction birikimi yok", data=data, query=query
        )


class MilvusConnections(_MilvusCheck):
    """Proxy bağlantı sayısı / proxy.maxConnectionNum.

    Eşik %80: limit dolunca yeni istemciler reddedilir. LIMIT Milvus'un varsayılanı (10000);
    tenant farklı ayarlıyorsa kurucuda değiştirilir. Metrik şu an Kafka yolunda yok → no_data.
    """

    name = "milvus_connections"
    LIMIT = 10000
    THRESHOLD = 0.80
    queries: ClassVar[dict[str, str]] = CHECK_QUERIES["milvus_connections"]

    def __init__(self, queries: dict[str, str] | None = None, limit: int | None = None) -> None:
        super().__init__(queries)
        self.limit = limit or self.LIMIT

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = self.render("connections", ctx, ns)
        samples = await m.query(query, ctx.window.end)
        if not samples:
            return self.evidence(ctx, Status.NO_DATA, f"{ns}: bağlantı metriği yok", query=query)
        rows: list[dict[str, Any]] = [
            {
                "pod": s.labels.get("pod"),
                "connections": s.value,
                "ratio": round(s.value / self.limit, 3),
            }
            for s in samples
        ]
        bad = [r for r in rows if r["ratio"] > self.THRESHOLD]
        if bad:
            return self.evidence(
                ctx,
                Status.ANOMALY,
                f"{ns}: "
                + ", ".join(f"{r['pod']} {r['connections']:.0f}/{self.limit}" for r in bad),
                data=rows,
                query=query,
            )
        return self.evidence(
            ctx,
            Status.OK,
            f"{ns}: bağlantılar limitin {pct(self.THRESHOLD)} altında",
            data=rows,
            query=query,
        )
