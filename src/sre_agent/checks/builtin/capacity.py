"""Kapasite kontrolleri (metrics): golden signals baseline, throttling, bellek, HPA, PVC, ephemeral.

Sorgulardaki yer tutucular: {ns} alarmın namespace'i, {range} pencere uzunluğu, {offset}
baseline için PromQL offset'i (boş ya da " offset 1w"). Sorgular ortama göre değiştirilebilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.checks.builtin._common import GIB, namespace, over, pct, prom_range
from sre_agent.ports import Capability, MetricsPort


class _NsMetricsCheck(Check):
    """Alarmın namespace'i üzerinde PromQL koşan kontrollerin ortak tabanı."""

    _base = True
    capability = Capability.METRICS
    categories = ("capacity_performance",)

    def render(self, key: str, ctx: CheckContext, ns: str, offset: str = "") -> str:
        return self.q[key].format(ns=ns, range=prom_range(ctx), offset=offset)

    async def run(self, ctx: CheckContext) -> Evidence:
        ns = namespace(ctx)
        if not ns:
            return self.evidence(ctx, Status.NO_DATA, "alarmda namespace etiketi yok")
        return await self.check(ctx, ctx.metrics(), ns)

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        raise NotImplementedError


# --------------------------------------------------------------------------- golden signals


@dataclass(frozen=True)
class Signal:
    name: str
    query: str  # {ns}, {range}, {offset}
    min_abs: float  # bunun altındaki değerler anomali sayılmaz (baseline ~0 iken gürültü)
    unit: str


# Çekirdekte ortamdan bağımsız sinyaller. İstek hızı / hata / gecikme uygulamaya özgüdür;
# domain paketleri kendi sinyallerini `signals=` ile ekler.
DEFAULT_SIGNALS = (
    Signal(
        "cpu_cores",
        'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}",container!=""}}'
        "[{range}]{offset}))",
        0.05,
        "core",
    ),
    Signal(
        "memory_bytes",
        'sum(avg_over_time(container_memory_working_set_bytes{{namespace="{ns}",container!=""}}'
        "[{range}]{offset}))",
        64 * 1024**2,
        "B",
    ),
    Signal(
        "network_rx_bytes_per_s",
        'sum(rate(container_network_receive_bytes_total{{namespace="{ns}"}}[{range}]{offset}))',
        100 * 1024,
        "B/s",
    ),
    Signal(
        "restarts",
        'sum(increase(kube_pod_container_status_restarts_total{{namespace="{ns}"}}'
        "[{range}]{offset}))",
        3,
        "adet",
    ),
)


class GoldenSignalsBaseline(_NsMetricsCheck):
    """Namespace'in sinyallerini geçmişteki aynı pencereyle karşılaştırır.

    Baseline: önce 1 hafta önce (haftalık döngüyü yakalar), Prometheus'ta o kadar eski veri
    yoksa 1 gün önce, o da yoksa no_data. Veri varlığı `count(up offset X)` ile sınanır.
    Anomali: güncel > FACTOR x baseline ve güncel > sinyalin mutlak alt sınırı.
    FACTOR=2: plandaki "baseline'ın 2 katı" ölçütü; tek başına günlük dalgalanmaları elemeye yeter.
    """

    name = "golden_signals_baseline"
    FACTOR = 2.0
    OFFSETS = ("1w", "1d")
    queries: ClassVar[dict[str, str]] = {"availability": "count(up{offset})"}

    def __init__(
        self, queries: dict[str, str] | None = None, signals: tuple[Signal, ...] = DEFAULT_SIGNALS
    ) -> None:
        super().__init__(queries)
        self.signals = signals

    async def _baseline_offset(self, ctx: CheckContext, m: MetricsPort) -> str | None:
        for off in self.OFFSETS:
            probe = self.q["availability"].format(offset=f" offset {off}")
            if await m.query(probe, ctx.window.end):
                return off
        return None

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        off = await self._baseline_offset(ctx, m)
        if off is None:
            return self.evidence(ctx, Status.NO_DATA, "baseline için yeterince eski veri yok")
        rows: list[dict[str, Any]] = []
        for s in self.signals:
            now = await m.query(
                s.query.format(ns=ns, range=prom_range(ctx), offset=""), ctx.window.end
            )
            then = await m.query(
                s.query.format(ns=ns, range=prom_range(ctx), offset=f" offset {off}"),
                ctx.window.end,
            )
            if not now:
                continue
            current = now[0].value
            base = then[0].value if then else 0.0
            rows.append(
                {
                    "signal": s.name,
                    "unit": s.unit,
                    "current": round(current, 4),
                    "baseline": round(base, 4),
                    "anomaly": current > self.FACTOR * base and current > s.min_abs,
                }
            )
        # Kanıttaki sorgular çalıştırılanlardır (şablon değil); baseline offset'i ayrıca yazılır.
        query = (
            "\n".join(s.query.format(ns=ns, range=prom_range(ctx), offset="") for s in self.signals)
            + f"\nbaseline: aynı sorgular 'offset {off}' ile"
        )
        if not rows:
            return self.evidence(ctx, Status.NO_DATA, f"{ns}: sinyal verisi yok", query=query)
        bad = [r for r in rows if r["anomaly"]]
        data = {"baseline_offset": off, "signals": rows}
        if not bad:
            return self.evidence(
                ctx, Status.OK, f"{ns}: sinyaller {off} öncesiyle uyumlu", data=data, query=query
            )
        parts = [f"{r['signal']} {r['current']:g} (baseline {r['baseline']:g})" for r in bad]
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{ns}: {off} öncesinin {self.FACTOR:g} katı: {'; '.join(parts)}",
            data=data,
            query=query,
        )


# --------------------------------------------------------------------------- container kaynakları


class CpuThrottling(_NsMetricsCheck):
    """CFS throttling oranı: throttled / toplam periyot.

    Eşik %25: bu orandan sonra gecikmeye yansıyan throttling tipik; altı çoğunlukla patlamalı yük.
    CPU limiti olmayan container'larda CFS periyodu yoktur → veri yoksa no_data.
    """

    name = "cpu_throttling"
    THRESHOLD = 0.25
    queries: ClassVar[dict[str, str]] = {
        "ratio": (
            "sum by (pod, container) (increase(container_cpu_cfs_throttled_periods_total"
            '{{namespace="{ns}",container!=""}}[{range}]))'
            " / sum by (pod, container) (increase(container_cpu_cfs_periods_total"
            '{{namespace="{ns}",container!=""}}[{range}]))'
        )
    }

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = self.render("ratio", ctx, ns)
        samples = [
            s for s in await m.query(query, ctx.window.end) if s.value == s.value
        ]  # NaN'ı at
        if not samples:
            return self.evidence(
                ctx, Status.NO_DATA, f"{ns}: CFS verisi yok (CPU limiti yok?)", query=query
            )
        bad = over(samples, self.THRESHOLD)
        if not bad:
            return self.evidence(
                ctx, Status.OK, f"{ns}: throttling {pct(self.THRESHOLD)} altında", query=query
            )
        top = bad[0]
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{ns}: {len(bad)} container throttled > {pct(self.THRESHOLD)} "
            f"(en yüksek {top.get('pod')}/{top.get('container')} {pct(float(top['ratio']))})",
            data=bad,
            query=query,
        )


class MemoryNearLimit(_NsMetricsCheck):
    """Penceredeki en yüksek working set / bellek limiti.

    Eşik %90: working set limite yaklaştıkça OOMKill riski; %90 üstü kısa bir yük artışıyla aşılır.
    Limiti olmayan container'lar sonuçta yer almaz.
    """

    name = "memory_near_limit"
    THRESHOLD = 0.90
    queries: ClassVar[dict[str, str]] = {
        "ratio": (
            "max by (pod, container) (max_over_time(container_memory_working_set_bytes"
            '{{namespace="{ns}",container!=""}}[{range}]))'
            " / max by (pod, container) (kube_pod_container_resource_limits"
            '{{namespace="{ns}",resource="memory"}})'
        )
    }

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = self.render("ratio", ctx, ns)
        samples = await m.query(query, ctx.window.end)
        if not samples:
            return self.evidence(
                ctx, Status.NO_DATA, f"{ns}: bellek limiti tanımlı container yok", query=query
            )
        bad = over(samples, self.THRESHOLD)
        if not bad:
            top = max(samples, key=lambda s: s.value)
            return self.evidence(
                ctx, Status.OK, f"{ns}: en yüksek bellek/limit {pct(top.value)}", query=query
            )
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{ns}: {len(bad)} container belleği limitin {pct(self.THRESHOLD)}'ının üzerinde "
            f"(en yüksek {bad[0].get('pod')}/{bad[0].get('container')} {pct(bad[0]['ratio'])})",
            data=bad,
            query=query,
        )


class HpaSaturation(_NsMetricsCheck):
    """HPA en fazla replikaya ulaşmış: yük artışına ölçeklenerek cevap verilemiyor.

    Eşik: current_replicas >= max_replicas (doygunluğun tanımı). HPA yoksa no_data.
    """

    name = "hpa_saturation"
    queries: ClassVar[dict[str, str]] = {
        "exists": 'kube_horizontalpodautoscaler_spec_max_replicas{{namespace="{ns}"}}',
        "saturated": (
            'kube_horizontalpodautoscaler_status_current_replicas{{namespace="{ns}"}}'
            " >= on (namespace, horizontalpodautoscaler)"
            ' kube_horizontalpodautoscaler_spec_max_replicas{{namespace="{ns}"}}'
        ),
    }

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = self.render("saturated", ctx, ns)
        if not await m.query(self.render("exists", ctx, ns), ctx.window.end):
            return self.evidence(ctx, Status.NO_DATA, f"{ns}: HPA yok", query=query)
        saturated = await m.query(query, ctx.window.end)
        if not saturated:
            return self.evidence(ctx, Status.OK, f"{ns}: HPA'lar doygun değil", query=query)
        names = sorted(s.labels.get("horizontalpodautoscaler", "?") for s in saturated)
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{ns}: {len(names)} HPA en fazla replikada: {', '.join(names)}",
            data=[{**s.labels, "replicas": s.value} for s in saturated],
            query=query,
        )


# --------------------------------------------------------------------------- disk


class PvcUsage(_NsMetricsCheck):
    """PVC doluluğu ve dolma tahmini.

    Eşik %80: büyütme/temizlik için zaman bırakan erken uyarı (alarm kuralıyla aynı).
    Tahmin: son 6 saatin doğrusal eğilimiyle 24 saat içinde boş alan < 0 → dolacak; yalnız
    doluluk FILL_MIN'in üstündeyse sayılır. Yeniden oluşan pod'un diski ilk dakikalarda 0'dan
    hızla büyür (ör. etcd: %4) ve kısa eğilim "24 saatte dolacak" der (2026-10-09, spot node
    kayıpları); yarısı dolmamış diskte bu tahmin güvenilir değildir.
    """

    name = "pvc_usage"
    THRESHOLD = 0.80
    FILL_MIN = 0.50
    queries: ClassVar[dict[str, str]] = {
        "ratio": (
            'kubelet_volume_stats_used_bytes{{namespace="{ns}"}}'
            ' / kubelet_volume_stats_capacity_bytes{{namespace="{ns}"}}'
        ),
        "fill_24h": (
            'predict_linear(kubelet_volume_stats_available_bytes{{namespace="{ns}"}}[6h], 86400)'
            " < 0"
        ),
    }

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = self.render("ratio", ctx, ns) + "\n" + self.render("fill_24h", ctx, ns)
        ratios = await m.query(self.render("ratio", ctx, ns), ctx.window.end)
        if not ratios:
            return self.evidence(ctx, Status.NO_DATA, f"{ns}: PVC yok", query=query)
        filling = {
            s.labels.get("persistentvolumeclaim")
            for s in await m.query(self.render("fill_24h", ctx, ns), ctx.window.end)
        }
        rows: list[dict[str, Any]] = [
            {
                "pvc": s.labels.get("persistentvolumeclaim"),
                "ratio": round(s.value, 3),
                "fills_in_24h": s.labels.get("persistentvolumeclaim") in filling
                and s.value >= self.FILL_MIN,
            }
            for s in ratios
        ]
        bad = [r for r in rows if r["ratio"] > self.THRESHOLD or r["fills_in_24h"]]
        if not bad:
            top = max(rows, key=lambda r: float(r["ratio"]))
            return self.evidence(
                ctx,
                Status.OK,
                f"{ns}: en dolu PVC {top['pvc']} {pct(float(top['ratio']))}",
                data=rows,
                query=query,
            )
        desc = ", ".join(
            f"{r['pvc']} {pct(r['ratio'])}"
            + (" (24 sa içinde dolacak)" if r["fills_in_24h"] else "")
            for r in bad
        )
        return self.evidence(ctx, Status.ANOMALY, f"{ns}: {desc}", data=rows, query=query)


class EphemeralStorageUsage(_NsMetricsCheck):
    """Pod başına ephemeral kullanım ve sizeLimit'li emptyDir doluluğu.

    Eşik %80 / 1Gi: pod başı ephemeral sınırı 1Gi; aşılırsa pod evict edilir.
    emptyDir için %80: sizeLimit aşılınca evict (ör. 384Mi'lık bir log emptyDir'i).
    Metrikler GKE'de k8s-ephemeral-storage-metrics'ten (ADR-016); OpenShift'te sorgu değişebilir.
    """

    name = "ephemeral_storage_usage"
    THRESHOLD = 0.80
    LIMIT_BYTES = GIB
    queries: ClassVar[dict[str, str]] = {
        "pod": 'max by (pod_name) (ephemeral_storage_pod_usage{{pod_namespace="{ns}"}})',
        "emptydir_pct": (
            "max by (pod_name, volume_name) "
            '(ephemeral_storage_container_volume_limit_percentage{{pod_namespace="{ns}"}})'
        ),
    }

    async def check(self, ctx: CheckContext, m: MetricsPort, ns: str) -> Evidence:
        query = self.render("pod", ctx, ns) + "\n" + self.render("emptydir_pct", ctx, ns)
        pods = await m.query(self.render("pod", ctx, ns), ctx.window.end)
        vols = await m.query(self.render("emptydir_pct", ctx, ns), ctx.window.end)
        if not pods and not vols:
            return self.evidence(ctx, Status.NO_DATA, f"{ns}: ephemeral metriği yok", query=query)
        bad: list[dict[str, Any]] = [
            {
                "pod": s.labels.get("pod_name"),
                "usage_ratio_1gi": round(s.value / self.LIMIT_BYTES, 3),
            }
            for s in pods
            if s.value / self.LIMIT_BYTES > self.THRESHOLD
        ] + [
            {
                "pod": s.labels.get("pod_name"),
                "volume": s.labels.get("volume_name"),
                "percent": round(s.value, 1),
            }
            for s in vols
            if s.value > self.THRESHOLD * 100
        ]
        if not bad:
            return self.evidence(
                ctx, Status.OK, f"{ns}: ephemeral kullanım sınırların altında", query=query
            )
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{ns}: {len(bad)} pod/volume ephemeral sınırının {pct(self.THRESHOLD)}'ına yaklaştı",
            data=bad,
            query=query,
        )
