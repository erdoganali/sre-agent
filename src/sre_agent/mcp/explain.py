"""Araç cevabını açıklayan ortak parçalar: boş sonucun sebebi ve öne çıkanlar (T3.5).

Boş sonuç model için belirsizdir: "sorun yok" mu, "veri silinmiş" mi, "yanlış ad" mı? Bir kontrol
sorgusuyla hangisi olduğu söylenir. Öne çıkanlar kodla seçilir (eşik aşan satırlar) ve yalnız
gözlemdir; sonuç (kök neden) modelin işidir.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sre_agent.mcp.common import ts as fmt_ts
from sre_agent.ports import K8sPort, LogsPort, MetricsPort, TracesPort

MAX_NAMES = 25


def highlights(lines: Iterable[str]) -> str:
    """Cevabın başına konan kısa liste; yoksa açıkça "öne çıkan yok"."""
    items = list(lines)
    if not items:
        return "Öne çıkan yok (eşik aşan satır yok).\n\n"
    return "Öne çıkanlar:\n" + "\n".join(f"- {x}" for x in items) + "\n\n"


async def why_empty_metrics(prom: MetricsPort, at: datetime, namespace: str | None = None) -> str:
    """Metrik sorgusu boş dönünce sebebi: o anda hiç veri yok mu, namespace yok mu, eşleşme mi."""
    if not await prom.query("count(up)", at):
        return (
            f"Sebep: {fmt_ts(at)} anında Prometheus'ta hiç veri yok. Bu zaman saklama süresinin "
            "dışında olabilir (eski olay) ya da Prometheus o anda çalışmıyordu; bu sonuç "
            '"sorun yok" anlamına gelmez.'
        )
    if namespace:
        seen = await prom.query(
            f'count(max_over_time(kube_pod_info{{namespace="{namespace}"}}[1d]))', at
        )
        if not seen:
            rows = await prom.query("count by (namespace) (kube_pod_info)", at)
            names = sorted(r.labels.get("namespace", "") for r in rows)[:MAX_NAMES]
            return (
                f"Sebep: '{namespace}' namespace'inde {fmt_ts(at)} öncesindeki 1 günde hiç pod "
                f"yok; ad yanlış olabilir. O anda pod'u olan namespace'ler: {', '.join(names)}"
            )
    return (
        "Sebep: veri var ama sorgu eşleşmedi (metrik adı, etiket ya da değer bu anda yok). "
        "Metrik ve etiketleri metrics.discover ile kontrol et."
    )


async def why_empty_logs(
    logs: LogsPort, start: datetime, stop: datetime, probe: str = '{service_name=~".+"}'
) -> str:
    if not await logs.query_range(probe, start, stop, 1):
        return (
            f"Sebep: {fmt_ts(start)} → {fmt_ts(stop)} penceresinde hiç log yok. Pencere saklama "
            'süresinin dışında olabilir; bu sonuç "hata yok" anlamına gelmez.'
        )
    return "Sebep: bu pencerede log var ama seçici ya da filtre eşleşmedi."


async def why_empty_traces(traces: TracesPort, start: datetime, stop: datetime) -> str:
    if not await traces.search("{}", start, stop, 1):
        return (
            f"Sebep: {fmt_ts(start)} → {fmt_ts(stop)} penceresinde hiç trace yok (saklama süresi "
            "dışında olabilir ya da bu sistemler trace göndermiyor)."
        )
    return "Sebep: bu pencerede trace var ama TraceQL eşleşmedi."


async def why_empty_k8s(k8s: K8sPort, namespace: str | None, kind: str) -> str:
    if namespace and not await k8s.list("Namespace", fields={"metadata.name": namespace}):
        return f"Sebep: '{namespace}' namespace'i yok (ad yanlış olabilir)."
    where = f"'{namespace}' namespace'inde" if namespace else "kümede"
    return (
        f"Sebep: {where} şu anda {kind} yok. Kubernetes yalnız bugünü bilir; olay anı için "
        "k8s.placement ya da metrikler."
    )
