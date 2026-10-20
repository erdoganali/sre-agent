from __future__ import annotations

import builtins
from collections.abc import Iterable
from typing import Any

from lightkube import ALL_NS, AsyncClient
from lightkube.core.resource import NamespacedResource, Resource
from lightkube.generic_resource import create_namespaced_resource
from lightkube.resources.apps_v1 import (
    ControllerRevision,
    DaemonSet,
    Deployment,
    ReplicaSet,
    StatefulSet,
)
from lightkube.resources.autoscaling_v2 import HorizontalPodAutoscaler
from lightkube.resources.batch_v1 import CronJob, Job
from lightkube.resources.core_v1 import (
    ConfigMap,
    Endpoints,
    Event,
    Namespace,
    Node,
    PersistentVolumeClaim,
    Pod,
    PodLog,
    Service,
)
from lightkube.resources.networking_v1 import Ingress, NetworkPolicy

from sre_agent.ports import K8S_CUSTOM_KINDS, K8S_KINDS

_RESOURCES: dict[str, type[Resource]] = {
    "Pod": Pod,
    "Event": Event,
    "Node": Node,
    "Namespace": Namespace,
    "ConfigMap": ConfigMap,
    "PersistentVolumeClaim": PersistentVolumeClaim,
    "Service": Service,
    "Endpoints": Endpoints,
    "Deployment": Deployment,
    "ReplicaSet": ReplicaSet,
    "ControllerRevision": ControllerRevision,
    "StatefulSet": StatefulSet,
    "DaemonSet": DaemonSet,
    "Job": Job,
    "CronJob": CronJob,
    "Ingress": Ingress,
    "NetworkPolicy": NetworkPolicy,
    "HorizontalPodAutoscaler": HorizontalPodAutoscaler,
}
assert set(_RESOURCES) == K8S_KINDS


def _custom_resources(
    kinds: Iterable[tuple[str, str, str, str]],
) -> dict[tuple[str, str], Any]:
    return {
        (group, kind): create_namespaced_resource(
            group, version, kind, plural, verbs=["get", "list"]
        )
        for group, version, kind, plural in kinds
    }


class KubernetesReader:
    """K8sPort: yalnızca list (get/list/watch yetkili read-only ServiceAccount ile)."""

    def __init__(
        self,
        client: AsyncClient | None = None,
        extra_custom_kinds: Iterable[tuple[str, str, str, str]] = (),
    ):
        """`extra_custom_kinds`: domain paketlerinin eklediği (grup, sürüm, kind, plural)."""
        self._client = client or AsyncClient()
        self._custom = _custom_resources([*K8S_CUSTOM_KINDS, *extra_custom_kinds])

    async def list(
        self,
        kind: str,
        namespace: str | None = None,
        labels: dict[str, str] | None = None,
        fields: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if kind not in _RESOURCES:
            raise ValueError(f"okunamayan tür: {kind}")
        # lightkube'un list() aşırı yüklemeleri tür dinamik seçilince tiplenemiyor.
        res: Any = _RESOURCES[kind]
        kwargs: dict[str, Any] = {"labels": labels, "fields": fields}
        if issubclass(res, NamespacedResource):
            kwargs["namespace"] = namespace or ALL_NS
        return [obj.to_dict() async for obj in self._client.list(res, **kwargs)]

    async def list_custom(
        self, group: str, kind: str, namespace: str | None = None
    ) -> builtins.list[dict[str, Any]]:
        res = self._custom.get((group, kind))
        if res is None:
            raise ValueError(f"okunamayan tür: {group}/{kind}")
        return [
            obj.to_dict() async for obj in self._client.list(res, namespace=namespace or ALL_NS)
        ]

    async def pod_logs(
        self,
        namespace: str,
        pod: str,
        container: str | None = None,
        previous: bool = False,
        tail_lines: int = 200,
    ) -> builtins.list[str]:
        # lightkube.AsyncClient.log() `previous` parametresini açmıyor; aynı isteği kendi iç
        # istemcisiyle `previous` ekleyerek kuruyoruz (lightkube iç API'si: yükseltmede sınanmalı).
        inner: Any = self._client._client
        br = inner.prepare_request(
            "get",
            PodLog,
            name=pod,
            namespace=namespace,
            params={"container": container, "previous": previous, "tailLines": tail_lines},
        )
        resp = await inner.send(inner.build_adapter_request(br))
        inner.raise_for_status(resp)
        text: str = resp.text
        return text.splitlines()

    async def aclose(self) -> None:
        await self._client.close()
