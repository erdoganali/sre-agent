"""Ağ kontrolleri: DNS ve bağlantı hataları, probe zaman aşımları, NetworkPolicy değişiklikleri,
ingress 5xx."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, ClassVar

from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.checks.builtin._common import namespace, pct, prom_range, ts
from sre_agent.checks.builtin.application import clip
from sre_agent.ports import Capability


class DnsErrors(Check):
    """Küme DNS'inin (GKE: kube-dns/dnsmasq) pencere içindeki hata sayısı.

    Eşik 10: tekil hatalar (ör. var olmayan bir adın sorgusu) olağan; pencerede 10'dan fazlası
    çözümleme sorununa işaret eder. OpenShift'te CoreDNS kullanıldığı için sorgu değiştirilir.
    """

    name = "dns_errors"
    capability = Capability.METRICS
    categories = ("network",)
    THRESHOLD = 10
    queries: ClassVar[dict[str, str]] = {
        "errors": (
            "sum(increase(kubedns_dnsmasq_errors[{range}]))"
            " + sum(increase(kubedns_probe_dnsmasq_errors[{range}]))"
        ),
    }

    async def run(self, ctx: CheckContext) -> Evidence:
        query = self.q["errors"].format(range=prom_range(ctx))
        result = await ctx.metrics().query(query, ctx.window.end)
        if not result:
            return self.evidence(ctx, Status.NO_DATA, "DNS metriği yok", query=query)
        errors = result[0].value
        status = Status.ANOMALY if errors > self.THRESHOLD else Status.OK
        return self.evidence(
            ctx, status, f"pencerede {errors:.0f} DNS hatası", data={"errors": errors}, query=query
        )


class ConnectionErrors(Check):
    """Loglardaki bağlantı hataları: connection refused, i/o timeout, deadline exceeded, reset.

    Eşik 5 satır: tek tük yeniden denemeler olağan; pencerede 5'ten fazlası bağlantı sorunudur.
    Kapsam alarmın service_name etiketi, yoksa tüm servisler.
    """

    name = "connection_errors"
    capability = Capability.LOGS
    categories = ("network",)
    THRESHOLD = 5
    LIMIT = 500
    queries: ClassVar[dict[str, str]] = {
        "errors": (
            '{{service_name=~"{service}"}}'
            ' |~ "(?i)(connection refused|i/o timeout|deadline exceeded'
            '|connection reset|no route to host)"'
        ),
    }

    async def run(self, ctx: CheckContext) -> Evidence:
        service = ctx.labels.get("service_name", ".+")
        query = self.q["errors"].format(service=service)
        lines = await ctx.logs().query_range(query, ctx.window.start, ctx.window.end, self.LIMIT)
        if len(lines) <= self.THRESHOLD:
            return self.evidence(
                ctx, Status.OK, f"{len(lines)} bağlantı hatası satırı", query=query
            )
        by_service: dict[str, int] = {}
        for entry in lines:
            svc = entry.labels.get("service_name", "?")
            by_service[svc] = by_service.get(svc, 0) + 1
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{len(lines)} bağlantı hatası satırı "
            f"({', '.join(f'{k}: {v}' for k, v in by_service.items())})",
            data={"by_service": by_service, "examples": [clip(e.line) for e in lines[:5]]},
            query=query,
        )


class ProbeTimeouts(Check):
    """Kubelet'in probe'ları zaman aşımına uğruyor (Readiness/Liveness/Startup + timeout).

    Kaynak: pencere içindeki `Unhealthy` olayları. Yalnız zaman aşımı biçimindeki hatalar
    sayılır (context deadline exceeded, Client.Timeout, i/o timeout, timed out); uygulamanın
    döndüğü hata kodu (ör. "statuscode: 500") ya da reddedilen bağlantı sayılmaz: onlar uygulama
    ya da süreç sorunudur.
    Zaman aşımı ağ gecikmesinden de, aşırı yüklü uygulamadan da gelebilir; correlate adımı CPU
    throttling ve bellek kanıtıyla birlikte değerlendirir. Eşik: pencerede 3 olay (tek tük olağan).
    """

    name = "probe_timeouts"
    capability = Capability.K8S
    categories = ("network",)
    THRESHOLD = 3
    TIMEOUT = re.compile(
        r"(?i)(context deadline exceeded|Client\.Timeout|i/o timeout|timed out|timeout)"
    )

    async def run(self, ctx: CheckContext) -> Evidence:
        ns = namespace(ctx)
        if not ns:
            return self.evidence(ctx, Status.NO_DATA, "alarmda namespace etiketi yok")
        query = f"list Event reason=Unhealthy namespace={ns} (probe zaman aşımı)"
        by_pod: dict[str, int] = {}
        examples: list[str] = []
        for e in await ctx.k8s().list("Event", namespace=ns, fields={"reason": "Unhealthy"}):
            at = ts(e.get("lastTimestamp") or e.get("eventTime") or e.get("firstTimestamp"))
            if at is None or not ctx.window.start <= at <= ctx.window.end:
                continue
            msg = e.get("message") or e.get("note") or ""
            if "probe failed" not in msg.lower() or not self.TIMEOUT.search(msg):
                continue
            pod = (e.get("involvedObject") or {}).get("name", "?")
            by_pod[pod] = by_pod.get(pod, 0) + int(e.get("count") or 1)
            if len(examples) < 3:
                examples.append(clip(msg))
        total = sum(by_pod.values())
        if total < self.THRESHOLD:
            return self.evidence(ctx, Status.OK, f"{ns}: {total} probe zaman aşımı", query=query)
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{ns}: {total} probe zaman aşımı "
            f"({', '.join(f'{k}: {v}' for k, v in sorted(by_pod.items()))})",
            data={"by_pod": by_pod, "examples": examples},
            query=query,
        )


class NetworkPolicyChanges(Check):
    """Pencerede değişen NetworkPolicy'ler (kaynağı ne olursa olsun: elle, Helm, Argo CD).

    Eşik yok: ağ sorunu penceresinde bir NetworkPolicy değişikliği güçlü bir adaydır.
    """

    name = "networkpolicy_changes"
    capability = Capability.CHANGE
    categories = ("network",)

    async def run(self, ctx: CheckContext) -> Evidence:
        ns = namespace(ctx)
        if not ns:
            return self.evidence(ctx, Status.NO_DATA, "alarmda namespace etiketi yok")
        query = f"changes kind=NetworkPolicy namespace={ns}"
        found = [
            c
            for c in await ctx.changes().changes(ns, ctx.window.start, ctx.window.end)
            if c.kind == "NetworkPolicy"
        ]
        if not found:
            return self.evidence(
                ctx, Status.OK, f"{ns}: NetworkPolicy değişikliği yok", query=query
            )
        rows: list[dict[str, Any]] = []
        for c in found:
            row = asdict(c)
            row["at"] = c.at.isoformat()
            rows.append(row)
        names = sorted({f"{c.name} ({c.source})" for c in found})
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{ns}: NetworkPolicy değişti: {', '.join(names)}",
            data=rows,
            query=query,
        )


class Ingress5xx(Check):
    """Ingress (nginx) 5xx oranı.

    Eşik %5: birkaç yüzde altı arka uç yeniden başlatmalarında olağan; üstü kullanıcıya yansır.
    GKE'de ingress-nginx metrikleri toplanmıyor (paylaşılan kurulum, ADR-020) → no_data.
    """

    name = "ingress_5xx"
    capability = Capability.METRICS
    categories = ("network",)
    THRESHOLD = 0.05
    queries: ClassVar[dict[str, str]] = {
        "exists": 'count(nginx_ingress_controller_requests{{namespace="{ns}"}})',
        "ratio": (
            "sum by (ingress) (rate(nginx_ingress_controller_requests"
            '{{namespace="{ns}",status=~"5.."}}[{range}]))'
            " / sum by (ingress) (rate(nginx_ingress_controller_requests"
            '{{namespace="{ns}"}}[{range}]))'
        ),
    }

    async def run(self, ctx: CheckContext) -> Evidence:
        ns = namespace(ctx)
        if not ns:
            return self.evidence(ctx, Status.NO_DATA, "alarmda namespace etiketi yok")
        m = ctx.metrics()
        query = self.q["ratio"].format(ns=ns, range=prom_range(ctx))
        if not await m.query(self.q["exists"].format(ns=ns), ctx.window.end):
            return self.evidence(ctx, Status.NO_DATA, "ingress metriği yok", query=query)
        bad = [s for s in await m.query(query, ctx.window.end) if s.value > self.THRESHOLD]
        if not bad:
            return self.evidence(
                ctx, Status.OK, f"{ns}: ingress 5xx {pct(self.THRESHOLD)} altında", query=query
            )
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{ns}: " + ", ".join(f"{s.labels.get('ingress')} 5xx {pct(s.value)}" for s in bad),
            data=[{**s.labels, "ratio": round(s.value, 3)} for s in bad],
            query=query,
        )
