"""GKE'ye karşı read-only entegrasyon testleri. Çalıştırma: make test-integration.

Kubernetes erişimi, kullanıcının kendi kimliğiyle değil sre-agent-readonly ServiceAccount'unun
kısa ömürlü token'ıyla yapılır; böylece adaptörler gerçekten read-only kimlikle sınanır.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from _env import k8s_client, require_env
from lightkube import ApiError
from lightkube.resources.core_v1 import Secret

from sre_agent.adapters.k8s_changes import K8sChangeSource
from sre_agent.adapters.kubernetes import KubernetesReader
from sre_agent.adapters.loki import LokiLogs
from sre_agent.adapters.prometheus import PrometheusMetrics
from sre_agent.adapters.tempo import TempoTraces
from sre_agent.adapters.tls import TlsProbe
from sre_agent.checks import CheckContext, Status, Window, run_checks
from sre_agent.checks.builtin import (
    AuthFailures,
    ConnectionErrors,
    CpuThrottling,
    CrashLogException,
    DnsErrors,
    EphemeralStorageUsage,
    ErrorSpans,
    GoldenSignalsBaseline,
    HpaSaturation,
    Ingress5xx,
    KafkaConsumerLag,
    MemoryNearLimit,
    NetworkPolicyChanges,
    NewErrorSignatures,
    NodeConditions,
    NodeEvents,
    PendingPods,
    PodRestarts,
    PvcUsage,
    TlsCertificateExpiry,
)
from sre_agent.ports import Capability

pytestmark = pytest.mark.integration

require_env()

NOW = datetime.now(UTC)
WINDOW = Window(start=NOW - timedelta(hours=1), end=NOW)


@pytest.fixture
async def k8s() -> AsyncIterator[KubernetesReader]:
    reader = KubernetesReader(client=k8s_client())
    yield reader
    await reader.aclose()


async def test_prometheus_reads() -> None:
    samples = await PrometheusMetrics(os.environ["SRE_IT_PROMETHEUS_URL"]).query("up", NOW)
    assert samples, "up metriği yok"


async def test_loki_reads() -> None:
    lines = await LokiLogs(os.environ["SRE_IT_LOKI_URL"]).query_range(
        '{service_name=~".+"}', NOW - timedelta(days=6), NOW, 5
    )
    assert lines, "Loki'de son 6 günde log yok"


async def test_tempo_reads() -> None:
    # VectorDB trace'leri kapalı; boş liste geçerli, API hatasız cevap vermeli.
    traces = await TempoTraces(os.environ["SRE_IT_TEMPO_URL"]).search(
        "{}", NOW - timedelta(days=2), NOW, 5
    )
    assert isinstance(traces, list)


async def test_k8s_reads_with_readonly_serviceaccount(k8s: KubernetesReader) -> None:
    assert await k8s.list("Pod", namespace="monitoring")
    assert await k8s.list("Node")
    assert await k8s.list("Event", namespace="monitoring") is not None


async def test_readonly_serviceaccount_cannot_read_secrets() -> None:
    client = k8s_client()
    with pytest.raises(ApiError) as err:
        await client.get(Secret, "grafana-admin", namespace="monitoring")
    assert err.value.status.code == 403
    await client.close()


async def test_builtin_checks_end_to_end(k8s: KubernetesReader) -> None:
    ctx = CheckContext(
        labels={"namespace": "monitoring"},
        window=WINDOW,
        ports={
            Capability.METRICS: PrometheusMetrics(os.environ["SRE_IT_PROMETHEUS_URL"]),
            Capability.K8S: k8s,
        },
    )
    results = await run_checks([KafkaConsumerLag(), PodRestarts()], ctx)
    for r in results:
        print(f"{r.check}: {r.status} — {r.summary} ({r.duration_ms:.0f} ms)")
    assert all(r.status is not Status.ERROR for r in results), results


@pytest.mark.parametrize("namespace", ["monitoring", "acme"])
async def test_capacity_and_node_checks_on_real_data(k8s: KubernetesReader, namespace: str) -> None:
    ctx = CheckContext(
        labels={"namespace": namespace},
        window=WINDOW,
        ports={
            Capability.METRICS: PrometheusMetrics(os.environ["SRE_IT_PROMETHEUS_URL"]),
            Capability.K8S: k8s,
        },
    )
    checks = [
        GoldenSignalsBaseline(),
        CpuThrottling(),
        MemoryNearLimit(),
        HpaSaturation(),
        PvcUsage(),
        EphemeralStorageUsage(),
        PendingPods(),
        NodeConditions(),
        NodeEvents(),
    ]
    results = await run_checks(checks, ctx)
    print(f"\n== {namespace}")
    for r in results:
        print(f"[{r.status}] {r.check}: {r.summary} ({r.duration_ms:.0f} ms)")
    assert all(r.status is not Status.ERROR for r in results), [r.error for r in results]


@pytest.mark.parametrize("namespace", ["vectordb", "acme"])
async def test_application_network_security_checks_on_real_data(
    k8s: KubernetesReader, namespace: str
) -> None:
    host, port = os.environ["SRE_IT_TLS_ENDPOINT"].split(":")
    ctx = CheckContext(
        labels={"namespace": namespace},
        window=WINDOW,
        ports={
            Capability.METRICS: PrometheusMetrics(os.environ["SRE_IT_PROMETHEUS_URL"]),
            Capability.LOGS: LokiLogs(os.environ["SRE_IT_LOKI_URL"]),
            Capability.TRACES: TempoTraces(os.environ["SRE_IT_TEMPO_URL"]),
            Capability.K8S: k8s,
            Capability.CHANGE: K8sChangeSource(k8s),
            Capability.TLS: TlsProbe(),
        },
    )
    checks = [
        NewErrorSignatures(),
        CrashLogException(),
        ErrorSpans(),
        DnsErrors(),
        ConnectionErrors(),
        NetworkPolicyChanges(),
        Ingress5xx(),
        AuthFailures(),
        TlsCertificateExpiry(endpoints=((host, int(port), "telemetry-kafka-bootstrap.kafka.svc"),)),
    ]
    results = await run_checks(checks, ctx)
    print(f"\n== {namespace}")
    for r in results:
        print(f"[{r.status}] {r.check}: {r.summary} ({r.duration_ms:.0f} ms)")
    assert all(r.status is not Status.ERROR for r in results), [r.error for r in results]
