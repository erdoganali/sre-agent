"""VectorDB paketinin kontrolleri gerçek veriyle (acme): hiçbiri error dönmemeli."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _env import k8s_client, require_env

from sre_agent.adapters.kubernetes import KubernetesReader
from sre_agent.adapters.prometheus import PrometheusMetrics
from sre_agent.checks import CheckContext, Status, Window, run_checks
from sre_agent.checks.builtin import GoldenSignalsBaseline
from sre_agent.packs import CheckRegistry, load_config, load_packs
from sre_agent.ports import Capability

pytestmark = pytest.mark.integration
require_env()

REPO = Path(__file__).parents[2]


async def test_vectordb_pack_on_real_data() -> None:
    reg = CheckRegistry(load_packs(load_config(REPO / "config" / "gke.yaml"), REPO))
    now = datetime.now(UTC)
    k8s = KubernetesReader(client=k8s_client(), extra_custom_kinds=reg.custom_kinds)
    ctx = CheckContext(
        labels={"namespace": "acme"},
        window=Window(start=now - timedelta(hours=1), end=now),
        ports={
            Capability.METRICS: PrometheusMetrics(os.environ["SRE_IT_PROMETHEUS_URL"]),
            Capability.K8S: k8s,
        },
    )
    pack_checks = [cls() for name, cls in sorted(reg.checks.items()) if name.startswith("milvus_")]
    checks = [*pack_checks, GoldenSignalsBaseline(signals=reg.signals)]
    results = await run_checks(checks, ctx)
    await k8s.aclose()
    print("\n== acme (vectordb paketi)")
    for r in results:
        print(f"[{r.status}] {r.check}: {r.summary} ({r.duration_ms:.0f} ms)")
    assert all(r.status is not Status.ERROR for r in results), [r.error for r in results]
