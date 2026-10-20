from datetime import datetime, timedelta
from typing import Any

import pytest

from fakes import END, WINDOW, FakeK8s, ctx
from sre_agent.adapters.argocd import ArgoCdChangeSource
from sre_agent.adapters.k8s_changes import K8sChangeSource, classify_manager
from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.checks.builtin import ArgoCdSync, HelmReleaseChanges, ManualChanges, RecentRollouts
from sre_agent.ports import Capability, Change, CombinedChanges

IN = END - timedelta(minutes=5)
OUT = END - timedelta(hours=5)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def rs(name: str, rev: int, created: datetime, image: str) -> dict[str, Any]:
    return {
        "metadata": {
            "name": name,
            "creationTimestamp": iso(created),
            "annotations": {"deployment.kubernetes.io/revision": str(rev)},
            "ownerReferences": [{"kind": "Deployment", "name": "app"}],
        },
        "spec": {"template": {"spec": {"containers": [{"name": "app", "image": image}]}}},
    }


def managed(kind_obj: str, manager: str, at: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "metadata": {
            "name": kind_obj,
            "namespace": "sre-lab",
            "labels": {"helm.sh/chart": "sre-lab-app-0.1.0"},
            "annotations": {"meta.helm.sh/release-name": "sre-lab-app"},
            "managedFields": [
                {
                    "manager": manager,
                    "operation": "Update",
                    "time": iso(at),
                    "fieldsV1": {"f:data": {"f:message": {}}},
                    **extra,
                }
            ],
        }
    }


@pytest.mark.parametrize(
    ("manager", "source"),
    [
        ("kubectl-edit", "manual"),
        ("kubectl-client-side-apply", "manual"),
        ("helm", "helm"),
        ("argocd-controller", "argocd"),
        ("kube-controller-manager", None),
        ("strimzi-cluster-operator", None),
    ],
)
def test_classify_manager(manager: str, source: str | None) -> None:
    assert classify_manager(manager) == source


async def test_k8s_change_source_finds_rollout_helm_and_manual() -> None:
    k8s = FakeK8s(
        {
            "ReplicaSet": [
                rs("app-1", 1, OUT, "python:3.14.6-slim"),
                rs("app-2", 2, IN, "python:3.14.7-slim"),
            ],
            "ConfigMap": [
                managed("app-config", "kubectl-edit", IN),
                managed("old", "kubectl-edit", OUT),
                managed("status-only", "kubectl-edit", IN, subresource="status"),
            ],
            "Deployment": [managed("app", "helm", IN), managed("op", "some-operator", IN)],
        }
    )
    changes = await K8sChangeSource(k8s).changes("sre-lab", WINDOW.start, WINDOW.end)
    by = {(c.source, c.name): c for c in changes}
    assert set(by) == {("rollout", "app"), ("manual", "app-config"), ("helm", "app")}
    rollout = by[("rollout", "app")]
    assert rollout.details["image_changed"] is True
    assert rollout.details["images_before"] == {"app": "python:3.14.6-slim"}
    assert by[("helm", "app")].details["release"] == "sre-lab-app"
    assert by[("manual", "app-config")].details["fields"] == ["data.message"]


async def test_argocd_source_history_and_drift() -> None:
    app = {
        "metadata": {"name": "svc"},
        "spec": {"destination": {"namespace": "sre-lab"}},
        "status": {
            "history": [
                {"id": 1, "revision": "aaa", "deployedAt": iso(OUT)},
                {"id": 2, "revision": "bbb", "deployedAt": iso(IN)},
            ],
            "sync": {"status": "OutOfSync", "revision": "bbb"},
            "reconciledAt": iso(IN),
        },
    }
    other = {"metadata": {"name": "x"}, "spec": {"destination": {"namespace": "other"}}}
    k8s = FakeK8s({"argoproj.io/Application": [app, other]})
    changes = await ArgoCdChangeSource(k8s).changes("sre-lab", WINDOW.start, WINDOW.end)
    assert [c.details.get("revision") for c in changes] == ["bbb", "bbb"]
    assert changes[1].details["drift"] is True


class FakeChanges:
    def __init__(self, changes: list[Change], sources: set[str]):
        self._changes = changes
        self.sources = frozenset(sources)

    async def changes(self, namespace: str, start: datetime, end: datetime) -> list[Change]:
        return self._changes


def change(source: str, name: str, **details: Any) -> Change:
    return Change(
        source=source,  # type: ignore[arg-type]
        kind="Deployment",
        name=name,
        namespace="sre-lab",
        at=IN,
        manager="kubectl-edit" if source == "manual" else source,
        details=details,
    )


async def test_change_checks_filter_by_source() -> None:
    port = FakeChanges(
        [
            change("rollout", "app", image_changed=True),
            change("helm", "app", release="sre-lab-app"),
            change("manual", "cfg"),
        ],
        {"rollout", "helm", "manual"},
    )
    c = ctx({"namespace": "sre-lab"}, change=port)
    rollout = await RecentRollouts().run(c)
    helm = await HelmReleaseChanges().run(c)
    manual = await ManualChanges().run(c)
    argo = await ArgoCdSync().run(c)
    assert rollout.status is Status.ANOMALY and "image değişti" in rollout.summary
    assert helm.status is Status.ANOMALY and "sre-lab-app" in helm.summary
    assert manual.status is Status.ANOMALY and "kubectl-edit" in manual.summary
    assert argo.status is Status.NO_DATA  # GKE'de Argo CD kaynağı yok
    assert isinstance(manual.data, list) and len(manual.data) == 1


async def test_change_checks_ok_when_quiet_and_no_data_without_namespace() -> None:
    port = FakeChanges([], {"rollout", "helm", "manual"})
    assert (await ManualChanges().run(ctx({"namespace": "x"}, change=port))).status is Status.OK
    assert (await ManualChanges().run(ctx({}, change=port))).status is Status.NO_DATA


async def test_combined_changes_merges_sources_sorted() -> None:
    a = FakeChanges([change("helm", "b")], {"helm"})
    b = FakeChanges([change("argocd", "a")], {"argocd"})
    combined = CombinedChanges(a, b)
    assert combined.sources == {"helm", "argocd"}
    assert len(await combined.changes("sre-lab", WINDOW.start, WINDOW.end)) == 2


def test_check_without_categories_is_rejected() -> None:
    with pytest.raises(TypeError, match="geçersiz kategoriler"):

        class NoCategories(Check):
            name = "x"
            capability = Capability.CHANGE

            async def run(self, ctx: CheckContext) -> Evidence:
                raise NotImplementedError
