"""Uygulama kontrolleri: yeni hata imzaları (Loki), çöken container logu, hata span'ları (Tempo).

Log içeriği güvenilmez veridir: yalnızca kanıt olarak (kırpılarak) taşınır, içindeki hiçbir şey
yorumlanmaz ya da çalıştırılmaz.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from typing import Any, ClassVar

from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.checks.builtin._common import namespace, ts
from sre_agent.ports import Capability

MAX_LINE = 300  # kanıta konan tek satırın en fazla uzunluğu

_NORMALIZE = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<uuid>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ][\d:.]+(Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{8,}\b", re.I), "<hex>"),
    (re.compile(r'"[^"]{0,200}"'), '"<str>"'),
    (re.compile(r"\d+"), "<n>"),
]


def signature(line: str) -> str:
    """Değişken kısımları (id, zaman, ip, sayı, tırnaklı metin) atarak hata imzası çıkarır."""
    sig = line.strip().lower()
    for pattern, repl in _NORMALIZE:
        sig = pattern.sub(repl, sig)
    return re.sub(r"\s+", " ", sig)[:160]


def clip(line: str) -> str:
    return line if len(line) <= MAX_LINE else line[:MAX_LINE] + "…"


class NewErrorSignatures(Check):
    """Pencerede görülen ama 1 gün önceki aynı pencerede görülmeyen hata imzaları (Loki).

    Baseline 1 gün önce: günlük döngüdeki olağan hataları eler. Baseline penceresinde hiç log
    yoksa (veri yok) karşılaştırma yapılamaz → no_data. Eşik: en az 1 yeni imza; imza başına
    sayı ve örnek satır kanıta eklenir. Kapsam alarmın service_name etiketi, yoksa tüm servisler.
    """

    name = "new_error_signatures"
    capability = Capability.LOGS
    categories = ("application_service_bug",)
    LIMIT = 2000
    BASELINE = timedelta(days=1)
    queries: ClassVar[dict[str, str]] = {
        "errors": '{{service_name=~"{service}"}} |~ "(?i)(error|exception|panic|fatal|traceback)"',
        "any": '{{service_name=~"{service}"}}',
    }

    async def run(self, ctx: CheckContext) -> Evidence:
        service = re.escape(ctx.labels["service_name"]) if "service_name" in ctx.labels else ".+"
        logs = ctx.logs()
        errors_q = self.q["errors"].format(service=service)
        any_q = self.q["any"].format(service=service)
        b_start, b_end = ctx.window.start - self.BASELINE, ctx.window.end - self.BASELINE
        if not await logs.query_range(any_q, b_start, b_end, 1):
            return self.evidence(
                ctx, Status.NO_DATA, "baseline penceresinde (1 gün önce) log yok", query=errors_q
            )
        now = await logs.query_range(errors_q, ctx.window.start, ctx.window.end, self.LIMIT)
        before = await logs.query_range(errors_q, b_start, b_end, self.LIMIT)
        known = {signature(entry.line) for entry in before}
        counts: Counter[str] = Counter()
        example: dict[str, Any] = {}
        for entry in now:
            sig = signature(entry.line)
            if sig in known:
                continue
            counts[sig] += 1
            example.setdefault(
                sig, {"service": entry.labels.get("service_name"), "line": clip(entry.line)}
            )
        query = f"{errors_q}\nbaseline: 1 gün önce aynı pencere"
        if not counts:
            return self.evidence(
                ctx, Status.OK, f"yeni hata imzası yok ({len(now)} hata satırı)", query=query
            )
        rows = [{"signature": sig, "count": n, **example[sig]} for sig, n in counts.most_common(10)]
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{len(counts)} yeni hata imzası; en sık: "
            f"{rows[0]['signature'][:80]} (x{rows[0]['count']})",
            data=rows,
            query=query,
        )


class CrashLogException(Check):
    """Pencerede sonlanan container'ların önceki çalışmasının logunda exception/panic.

    Eşik yok: çöken bir container'ın son satırlarında hata imzası görmek kök nedenin kanıtıdır.
    OOMKilled gibi log dışı nedenler pod_restarts kontrolünde; burada yalnızca log kanıtı aranır.
    """

    name = "crash_log_exception"
    capability = Capability.K8S
    categories = ("application_service_bug",)
    TAIL = 200
    PATTERN = re.compile(r"(?i)(traceback|exception|panic:|fatal|segmentation fault|error:)")

    async def run(self, ctx: CheckContext) -> Evidence:
        ns = namespace(ctx)
        if not ns:
            return self.evidence(ctx, Status.NO_DATA, "alarmda namespace etiketi yok")
        k8s = ctx.k8s()
        crashed: list[tuple[str, str, str | None]] = []
        for pod in await k8s.list("Pod", namespace=ns):
            for cs in (pod.get("status") or {}).get("containerStatuses") or []:
                last = (cs.get("lastState") or {}).get("terminated") or {}
                finished = ts(last.get("finishedAt"))
                if finished and ctx.window.start <= finished <= ctx.window.end:
                    crashed.append((pod["metadata"]["name"], cs.get("name"), last.get("reason")))
        query = f"pods/log previous=true tail={self.TAIL} namespace={ns}"
        if not crashed:
            return self.evidence(
                ctx, Status.OK, f"{ns}: pencerede çöken container yok", query=query
            )
        rows: list[dict[str, Any]] = []
        for pod_name, container, reason in crashed:
            lines = await k8s.pod_logs(ns, pod_name, container, previous=True, tail_lines=self.TAIL)
            hits = [clip(line) for line in lines if self.PATTERN.search(line)]
            if hits:
                rows.append(
                    {
                        "pod": pod_name,
                        "container": container,
                        "reason": reason,
                        "lines": hits[-5:],
                    }
                )
        if not rows:
            return self.evidence(
                ctx,
                Status.OK,
                f"{ns}: {len(crashed)} container çöktü ama logda exception yok",
                query=query,
            )
        first = rows[0]
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{ns}: {first['pod']}/{first['container']} çöktü; "
            f"son hata: {first['lines'][-1][:120]}",
            data=rows,
            query=query,
        )


class ErrorSpans(Check):
    """Tempo'da pencere içindeki hata span'ları ve yavaş istemci çağrıları.

    Eşikler: en az 1 hata trace'i; istemci (bağımlılık) span'ı SLOW_MS'den uzun. SLOW_MS=2000:
    tipik bir bağımlılık çağrısı yüzlerce ms'dir; 2 sn üstü timeout sınırlarına yaklaşır.
    Pencerede hiç trace yoksa no_data (ör. uygulamanın trace gönderimi kapalı).
    """

    name = "error_spans"
    capability = Capability.TRACES
    categories = ("application_service_bug", "network")
    SLOW_MS = 2000
    LIMIT = 20
    queries: ClassVar[dict[str, str]] = {
        "any": "{{}}",
        "errors": "{{ status = error }}",
        "slow": "{{ kind = client && duration > {slow}ms }}",
    }

    async def run(self, ctx: CheckContext) -> Evidence:
        tempo = ctx.traces()
        start, end = ctx.window.start, ctx.window.end
        errors_q = self.q["errors"].format()
        slow_q = self.q["slow"].format(slow=self.SLOW_MS)
        query = f"{errors_q}\n{slow_q}"
        if not await tempo.search(self.q["any"].format(), start, end, 1):
            return self.evidence(ctx, Status.NO_DATA, "pencerede trace yok", query=query)
        errors = await tempo.search(errors_q, start, end, self.LIMIT)
        slow = await tempo.search(slow_q, start, end, self.LIMIT)
        if not errors and not slow:
            return self.evidence(
                ctx, Status.OK, "hata ya da yavaş bağımlılık span'ı yok", query=query
            )
        data = {
            "error_traces": [
                {"trace_id": t.trace_id, "service": t.root_service, "name": t.root_name}
                for t in errors
            ],
            "slow_client_traces": [
                {"trace_id": t.trace_id, "service": t.root_service, "duration_ms": t.duration_ms}
                for t in slow
            ],
        }
        services = sorted({str(t.root_service) for t in [*errors, *slow]})
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{len(errors)} hata trace'i, {len(slow)} yavaş bağımlılık trace'i "
            f"({', '.join(services)})",
            data=data,
            query=query,
        )
