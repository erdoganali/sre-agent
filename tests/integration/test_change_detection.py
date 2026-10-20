"""T1.3 kabulü: scripts/change-demo.sh'ın yaptığı helm upgrade ve kubectl edit rapora yansıyor mu.

Değişiklik kontrolleri read-only ServiceAccount token'ıyla koşar.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from _env import k8s_client, require_env

from sre_agent.adapters.k8s_changes import K8sChangeSource
from sre_agent.adapters.kubernetes import KubernetesReader
from sre_agent.checks import CheckContext, Status, Window, run_checks
from sre_agent.checks.builtin import ArgoCdSync, HelmReleaseChanges, ManualChanges, RecentRollouts
from sre_agent.ports import Capability

pytestmark = pytest.mark.integration
require_env("SRE_IT_CHANGE_SINCE", "SRE_IT_LAB_NS")


async def test_helm_upgrade_and_kubectl_edit_are_reported() -> None:
    since = datetime.fromtimestamp(int(os.environ["SRE_IT_CHANGE_SINCE"]), UTC)
    ns = os.environ["SRE_IT_LAB_NS"]
    reader = KubernetesReader(client=k8s_client())
    ctx = CheckContext(
        labels={"namespace": ns},
        window=Window(start=since, end=datetime.now(UTC) + timedelta(seconds=5)),
        ports={Capability.CHANGE: K8sChangeSource(reader)},
    )
    results = {
        r.check: r
        for r in await run_checks(
            [RecentRollouts(), HelmReleaseChanges(), ManualChanges(), ArgoCdSync()], ctx
        )
    }
    await reader.aclose()

    print()
    for r in results.values():
        print(f"[{r.status}] {r.check}: {r.summary}")
        for row in r.data if isinstance(r.data, list) else []:
            if isinstance(row, dict):
                print(
                    f"    {row['source']:8} {row['kind']}/{row['name']} {row['at']} "
                    f"{row.get('manager') or ''} {row['details']}"
                )

    rollout, helm = results["recent_rollouts"], results["helm_release_changes"]
    manual, argo = results["manual_changes"], results["argocd_sync"]
    assert rollout.status is Status.ANOMALY and "image değişti" in rollout.summary
    assert os.environ.get("SRE_IT_EXPECT_TAG", "") in str(rollout.data)
    assert helm.status is Status.ANOMALY and "sre-lab-app" in helm.summary
    assert manual.status is Status.ANOMALY and "kubectl-edit" in manual.summary
    assert "ConfigMap/sre-lab-app" in manual.summary
    assert argo.status is Status.NO_DATA
