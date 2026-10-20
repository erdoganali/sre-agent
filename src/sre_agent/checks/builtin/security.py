"""Güvenlik kontrolleri: 401/403 artışı ve TLS sertifika bitiş süresi."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.checks.builtin._common import namespace, prom_range
from sre_agent.ports import Capability


class AuthFailures(Check):
    """Kubernetes API'sine 401/403 cevaplarının 1 gün önceyle karşılaştırması.

    Eşik: güncel oran > 2 x baseline ve > 0.1/sn. 2 kat: plandaki baseline ölçütü; 0.1/sn alt
    sınırı, baseline ~0 iken tek tük reddi anomali saymaz. Uygulama katmanı 401/403'leri
    (ingress, veritabanı) ilgili metrikler varsa sorgu değiştirilerek eklenir.
    """

    name = "auth_failures"
    capability = Capability.METRICS
    categories = ("security",)
    FACTOR = 2.0
    MIN_RATE = 0.1
    queries: ClassVar[dict[str, str]] = {
        "rate": 'sum(rate(apiserver_request_total{{code=~"401|403"}}[{range}]{offset}))',
    }

    async def run(self, ctx: CheckContext) -> Evidence:
        m = ctx.metrics()
        rng = prom_range(ctx)
        now_q = self.q["rate"].format(range=rng, offset="")
        base_q = self.q["rate"].format(range=rng, offset=" offset 1d")
        now = await m.query(now_q, ctx.window.end)
        if not now:
            return self.evidence(ctx, Status.NO_DATA, "401/403 metriği yok", query=now_q)
        base = await m.query(base_q, ctx.window.end)
        current = now[0].value
        baseline = base[0].value if base else 0.0
        data = {"rate_per_s": round(current, 4), "baseline_per_s": round(baseline, 4)}
        query = f"{now_q}\nbaseline: offset 1d"
        if current > self.FACTOR * baseline and current > self.MIN_RATE:
            return self.evidence(
                ctx,
                Status.ANOMALY,
                f"401/403 oranı {current:.2f}/sn (1 gün önce {baseline:.2f}/sn)",
                data=data,
                query=query,
            )
        return self.evidence(
            ctx, Status.OK, f"401/403 oranı {current:.3f}/sn", data=data, query=query
        )


class TlsCertificateExpiry(Check):
    """TLS sertifikalarının bitişine kalan süre.

    Uç noktalar: alarmın namespace'indeki Ingress'lerin spec.tls host'ları (443) ve yapılandırmada
    verilenler. Secret okunmaz; yalnızca el sıkışma yapılır. Eşik 14 gün: yenileme için
    iki haftalık süre; süresi dolmuş sertifika da anomali.
    """

    name = "tls_certificate_expiry"
    capability = Capability.TLS
    categories = ("security",)
    MIN_REMAINING = timedelta(days=14)

    def __init__(
        self,
        queries: dict[str, str] | None = None,
        endpoints: tuple[tuple[str, int, str | None], ...] = (),
    ) -> None:
        super().__init__(queries)
        self.endpoints = endpoints  # (host, port, SNI)

    async def _targets(self, ctx: CheckContext) -> list[tuple[str, int, str | None]]:
        targets = list(self.endpoints)
        ns = namespace(ctx)
        if ns and ctx.ports.get(Capability.K8S) is not None:
            for ing in await ctx.k8s().list("Ingress", namespace=ns):
                for tls in (ing.get("spec") or {}).get("tls") or []:
                    targets.extend((h, 443, h) for h in tls.get("hosts") or [])
        return sorted(set(targets))

    async def run(self, ctx: CheckContext) -> Evidence:
        targets = await self._targets(ctx)
        query = "tls handshake: " + ", ".join(f"{h}:{p}" for h, p, _ in targets)
        if not targets:
            return self.evidence(ctx, Status.NO_DATA, "TLS uç noktası yok", query=query)
        probe = ctx.tls()
        rows: list[dict[str, Any]] = []
        for host, port, sni in targets:
            try:
                cert = await probe.certificate(host, port, sni)
            except (OSError, TimeoutError) as exc:
                rows.append({"endpoint": f"{host}:{port}", "error": f"{type(exc).__name__}: {exc}"})
                continue
            remaining = cert.not_after - ctx.window.end
            rows.append(
                {
                    "endpoint": cert.endpoint,
                    "subject": cert.subject,
                    "not_after": cert.not_after.isoformat(),
                    "days_left": round(remaining.total_seconds() / 86400, 1),
                    "expiring": remaining < self.MIN_REMAINING,
                }
            )
        checked = [r for r in rows if "error" not in r]
        if not checked:
            return self.evidence(
                ctx,
                Status.ERROR,
                "hiçbir uç noktaya TLS bağlantısı kurulamadı",
                data=rows,
                query=query,
            )
        bad = [r for r in checked if r["expiring"]]
        if not bad:
            soonest = min(checked, key=lambda r: float(r["days_left"]))
            return self.evidence(
                ctx,
                Status.OK,
                f"en yakın bitiş {soonest['endpoint']} {soonest['days_left']} gün",
                data=rows,
                query=query,
            )
        return self.evidence(
            ctx,
            Status.ANOMALY,
            ", ".join(f"{r['endpoint']} {r['days_left']} gün" for r in bad),
            data=rows,
            query=query,
        )
