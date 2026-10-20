import asyncio
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from lightkube import AsyncClient, KubeConfig

from fakes import END, WINDOW, FakeK8s, FakeLogs, FakeMetrics, FakeTls, FakeTraces, ctx
from sre_agent.adapters.kubernetes import KubernetesReader
from sre_agent.adapters.tls import TlsProbe
from sre_agent.checks import Status
from sre_agent.checks.builtin import (
    AuthFailures,
    ConnectionErrors,
    CrashLogException,
    DnsErrors,
    ErrorSpans,
    Ingress5xx,
    NetworkPolicyChanges,
    NewErrorSignatures,
    TlsCertificateExpiry,
)
from sre_agent.checks.builtin.application import MAX_LINE, signature
from sre_agent.ports import Certificate, Change, LogLine, Sample, TraceSummary

NS = {"namespace": "sre-lab"}


def rows(data: object) -> list[dict[str, Any]]:
    assert isinstance(data, list)
    return cast(list[dict[str, Any]], data)


def line(text: str, service: str = "svc") -> LogLine:
    return LogLine(labels={"service_name": service}, ts=END, line=text)


# ------------------------------------------------------------------ uygulama


def test_signature_normalizes_variable_parts() -> None:
    a = signature('ERROR 2026-09-26T10:00:00Z req 8f3a9c2e1b user "ali" id=42 from 10.0.0.1:5000')
    b = signature('error 2026-09-27T11:11:11Z req 1a2b3c4d5e user "veli" id=7 from 10.9.9.9:80')
    assert a == b


async def test_new_error_signatures_against_daily_baseline() -> None:
    def resolve(q: str, start: datetime, end: datetime) -> list[LogLine]:
        baseline = end <= WINDOW.start
        if "|~" not in q:  # "any" sorgusu: baseline'da veri var mı
            return [line("x")]
        old = [line("ERROR timeout calling milvus id=1")]
        return (
            old if baseline else [*old, line("panic: nil map id=9"), line("panic: nil map id=10")]
        )

    ev = await NewErrorSignatures().run(ctx(metrics=None, logs=FakeLogs(resolve)))
    assert ev.status is Status.ANOMALY and "1 yeni hata imzası" in ev.summary
    assert rows(ev.data)[0]["count"] == 2


async def test_new_error_signatures_without_baseline_is_no_data() -> None:
    ev = await NewErrorSignatures().run(ctx(logs=FakeLogs(lambda q, s, e: [])))
    assert ev.status is Status.NO_DATA


def crashed_pod(name: str, finished: datetime) -> dict[str, Any]:
    iso = finished.isoformat().replace("+00:00", "Z")
    return {
        "metadata": {"name": name},
        "status": {
            "containerStatuses": [
                {"name": "app", "lastState": {"terminated": {"reason": "Error", "finishedAt": iso}}}
            ]
        },
    }


async def test_crash_log_exception_reads_previous_logs_and_clips() -> None:
    long_line = "x" * 1000
    k8s = FakeK8s(
        {"Pod": [crashed_pod("web", END - timedelta(minutes=2))]},
        logs={
            ("web", "app", True): [
                "starting",
                "Traceback (most recent call last):",
                f"ValueError: bad config {long_line}",
            ]
        },
    )
    ev = await CrashLogException().run(ctx(NS, k8s=k8s))
    assert ev.status is Status.ANOMALY and "web/app çöktü" in ev.summary
    assert all(len(x) <= MAX_LINE + 1 for x in rows(ev.data)[0]["lines"])


async def test_crash_log_exception_ok_without_crash() -> None:
    k8s = FakeK8s({"Pod": [crashed_pod("web", END - timedelta(days=2))]})
    assert (await CrashLogException().run(ctx(NS, k8s=k8s))).status is Status.OK


def trace(tid: str, service: str = "svc", ms: float = 10) -> TraceSummary:
    return TraceSummary(
        trace_id=tid, root_service=service, root_name="op", start=END, duration_ms=ms
    )


async def test_error_spans() -> None:
    def resolve(q: str) -> list[TraceSummary]:
        if "status = error" in q:
            return [trace("e1")]
        if "duration" in q:
            return [trace("s1", ms=3500)]
        return [trace("any")]

    ev = await ErrorSpans().run(ctx(traces=FakeTraces(resolve)))
    assert ev.status is Status.ANOMALY and "1 hata trace'i, 1 yavaş" in ev.summary
    assert (await ErrorSpans().run(ctx(traces=FakeTraces()))).status is Status.NO_DATA


# ------------------------------------------------------------------ ağ


async def test_dns_errors_threshold() -> None:
    bad = FakeMetrics(resolver=lambda q: [Sample({}, 40)])
    ok = FakeMetrics(resolver=lambda q: [Sample({}, 2)])
    assert (await DnsErrors().run(ctx(metrics=bad))).status is Status.ANOMALY
    assert (await DnsErrors().run(ctx(metrics=ok))).status is Status.OK
    assert (await DnsErrors().run(ctx(metrics=FakeMetrics()))).status is Status.NO_DATA


async def test_connection_errors_threshold() -> None:
    many = [line("dial tcp: connection refused", "backend") for _ in range(8)]
    ev = await ConnectionErrors().run(ctx(logs=FakeLogs(lambda q, s, e: many)))
    assert ev.status is Status.ANOMALY and "backend: 8" in ev.summary
    few = FakeLogs(lambda q, s, e: many[:2])
    assert (await ConnectionErrors().run(ctx(logs=few))).status is Status.OK


class Changes:
    sources = frozenset({"manual", "helm", "rollout"})

    def __init__(self, changes: list[Change]):
        self._c = changes

    async def changes(self, namespace: str, start: datetime, end: datetime) -> list[Change]:
        return self._c


async def test_networkpolicy_changes() -> None:
    np = Change("manual", "NetworkPolicy", "deny-all", "sre-lab", END, "kubectl-edit")
    cm = Change("manual", "ConfigMap", "cfg", "sre-lab", END, "kubectl-edit")
    ev = await NetworkPolicyChanges().run(ctx(NS, change=Changes([np, cm])))
    assert ev.status is Status.ANOMALY and "deny-all (manual)" in ev.summary
    assert (await NetworkPolicyChanges().run(ctx(NS, change=Changes([cm])))).status is Status.OK


async def test_ingress_5xx() -> None:
    def resolve(q: str) -> list[Sample]:
        if q.startswith("count("):
            return [Sample({}, 1)]
        return [Sample({"ingress": "web"}, 0.2), Sample({"ingress": "api"}, 0.01)]

    ev = await Ingress5xx().run(ctx(NS, metrics=FakeMetrics(resolver=resolve)))
    assert ev.status is Status.ANOMALY and "web 5xx %20" in ev.summary and "api" not in ev.summary
    assert (await Ingress5xx().run(ctx(NS, metrics=FakeMetrics()))).status is Status.NO_DATA


# ------------------------------------------------------------------ güvenlik


async def test_auth_failures_against_daily_baseline() -> None:
    spike = FakeMetrics(resolver=lambda q: [Sample({}, 0.05 if "offset" in q else 2.0)])
    small = FakeMetrics(resolver=lambda q: [Sample({}, 0.0 if "offset" in q else 0.05)])
    assert (await AuthFailures().run(ctx(metrics=spike))).status is Status.ANOMALY
    assert (await AuthFailures().run(ctx(metrics=small))).status is Status.OK


def cert(endpoint: str, days: float) -> Certificate:
    return Certificate(endpoint, "CN=x", "CN=ca", END + timedelta(days=days))


async def test_tls_expiry_from_ingress_hosts_and_endpoints() -> None:
    k8s = FakeK8s({"Ingress": [{"spec": {"tls": [{"hosts": ["app.example"]}]}}]})
    tls = FakeTls(
        {"app.example:443": cert("app.example:443", 5), "kafka:9095": cert("kafka:9095", 300)}
    )
    check = TlsCertificateExpiry(endpoints=(("kafka", 9095, None),))
    ev = await check.run(ctx(NS, k8s=k8s, tls=tls))
    assert ev.status is Status.ANOMALY and "app.example:443 5.0 gün" in ev.summary
    assert "kafka" not in ev.summary


async def test_tls_expiry_unreachable_and_no_targets() -> None:
    check = TlsCertificateExpiry(endpoints=(("down", 443, None),))
    ev = await check.run(ctx(tls=FakeTls({})))
    assert ev.status is Status.ERROR
    assert (await TlsCertificateExpiry().run(ctx(tls=FakeTls({})))).status is Status.NO_DATA


# ------------------------------------------------------------------ adaptörler


def self_signed(tmp: Path, days: int) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    c = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp / "c.pem", tmp / "k.pem"
    cert_path.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


async def test_tls_probe_reads_certificate(tmp_path: Path) -> None:
    cert_path, key_path = self_signed(tmp_path, 10)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(cert_path, key_path)

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        w.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=sctx)
    port = server.sockets[0].getsockname()[1]
    async with server:
        got = await TlsProbe().certificate("127.0.0.1", port, "localhost")
    assert got.subject == "CN=localhost"
    assert timedelta(days=9) < got.not_after - datetime.now(UTC) < timedelta(days=11)


async def test_pod_logs_requests_previous_container() -> None:
    seen: list[str] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(str(req.url))
        return httpx2.Response(200, text="a\nb\n")

    cfg = KubeConfig.from_dict(
        {
            "clusters": [{"name": "c", "cluster": {"server": "https://k8s.test"}}],
            "users": [{"name": "u", "user": {"token": "t"}}],
            "contexts": [{"name": "x", "context": {"cluster": "c", "user": "u"}}],
            "current-context": "x",
        }
    )
    reader = KubernetesReader(client=AsyncClient(cfg, transport=httpx2.MockTransport(handler)))
    assert await reader.pod_logs("ns", "p", "c", previous=True, tail_lines=5) == ["a", "b"]
    assert "previous=true" in seen[0] and "tailLines=5" in seen[0]


def _unhealthy(pod: str, msg: str, minutes_ago: int, count: int = 1) -> dict[str, Any]:
    at = (END - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return {
        "involvedObject": {"kind": "Pod", "name": pod},
        "reason": "Unhealthy",
        "message": msg,
        "lastTimestamp": at,
        "count": count,
    }


async def test_probe_timeouts_count_only_timeouts_in_window() -> None:
    from sre_agent.checks.builtin import ProbeTimeouts

    timeout = (
        'Readiness probe failed: Get "http://10.0.0.5:8080/": context deadline exceeded'
        " (Client.Timeout exceeded while awaiting headers)"
    )
    events = [
        _unhealthy("app-1", timeout, 3, count=4),
        _unhealthy("app-1", "Readiness probe failed: HTTP probe failed with statuscode: 500", 2, 9),
        _unhealthy("app-1", "Liveness probe failed: dial tcp: connect: connection refused", 2, 9),
        _unhealthy("eski", timeout, 120, count=50),  # pencere dışında
    ]
    ev = await ProbeTimeouts().run(ctx({"namespace": "sre-lab"}, k8s=FakeK8s({"Event": events})))
    assert ev.status is Status.ANOMALY
    assert cast(dict[str, Any], ev.data)["by_pod"] == {"app-1": 4}
    assert "network" in ProbeTimeouts.categories

    few = [_unhealthy("app-1", timeout, 3, count=2)]
    ev = await ProbeTimeouts().run(ctx({"namespace": "sre-lab"}, k8s=FakeK8s({"Event": few})))
    assert ev.status is Status.OK
    ev = await ProbeTimeouts().run(ctx({}, k8s=FakeK8s()))
    assert ev.status is Status.NO_DATA
