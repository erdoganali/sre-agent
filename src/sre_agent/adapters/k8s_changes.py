"""ChangePort'un Kubernetes uygulaması (GKE).

Helm release geçmişi Secret'ta durduğu için okunmaz (ADR-019). Değişiklikler şuradan çıkarılır:
- Rollout: pencerede oluşan ReplicaSet / ControllerRevision; önceki revizyonla image farkı.
- managedFields: hangi yöneticinin (manager) nesneyi ne zaman güncellediği.
  kubectl-* → manual, helm → helm, argocd* → argocd; diğer kontrolcüler (operatörler) sayılmaz.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sre_agent.ports import Change, ChangeSource, K8sPort

# managedFields'ı izlenen türler (kullanıcı ya da dağıtım aracı tarafından değiştirilenler).
WATCHED_KINDS = (
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "CronJob",
    "ConfigMap",
    "Service",
    "Ingress",
    "NetworkPolicy",
    "HorizontalPodAutoscaler",
)
ROLLOUT_OWNERS = {"ReplicaSet": ("Deployment",), "ControllerRevision": ("StatefulSet", "DaemonSet")}


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def classify_manager(manager: str) -> ChangeSource | None:
    m = manager.lower()
    if m.startswith("kubectl"):
        return "manual"
    if m == "helm" or m.startswith("helm-"):
        return "helm"
    if m.startswith("argocd"):
        return "argocd"
    return None


def _owner(obj: dict[str, Any], kinds: tuple[str, ...]) -> tuple[str, str] | None:
    for ref in obj["metadata"].get("ownerReferences") or []:
        if ref.get("kind") in kinds:
            return ref["kind"], ref["name"]
    return None


def _images(template: dict[str, Any] | None) -> dict[str, str]:
    spec = ((template or {}).get("spec") or {}).get("containers") or []
    return {c.get("name", "?"): c.get("image", "?") for c in spec}


def _revision(obj: dict[str, Any]) -> int:
    if "revision" in obj:  # ControllerRevision
        return int(obj["revision"])
    ann = obj["metadata"].get("annotations") or {}
    return int(ann.get("deployment.kubernetes.io/revision", 0))


def _template(obj: dict[str, Any]) -> dict[str, Any] | None:
    if "revision" in obj:  # ControllerRevision: data.spec.template
        return ((obj.get("data") or {}).get("spec") or {}).get("template")
    template: dict[str, Any] | None = (obj.get("spec") or {}).get("template")
    return template


def _changed_paths(fields_v1: dict[str, Any] | None) -> list[str]:
    """managedFields'tan değişen üst düzey alanlar (ör. data, spec.replicas)."""
    out: list[str] = []
    for top, sub in (fields_v1 or {}).items():
        if not top.startswith("f:") or top == "f:metadata":
            continue
        name = top[2:]
        children = [k[2:] for k in (sub or {}) if k.startswith("f:")]
        if children:
            out.extend(f"{name}.{c}" for c in children[:5])
        else:
            out.append(name)
    return sorted(out)


class K8sChangeSource:
    sources = frozenset({"rollout", "helm", "manual"})

    def __init__(self, k8s: K8sPort):
        self._k8s = k8s

    async def changes(self, namespace: str, start: datetime, end: datetime) -> list[Change]:
        out = await self._rollouts(namespace, start, end)
        for kind in WATCHED_KINDS:
            for obj in await self._k8s.list(kind, namespace=namespace):
                out.extend(self._managed_changes(kind, obj, start, end))
        return sorted(out, key=lambda c: c.at)

    async def _rollouts(self, namespace: str, start: datetime, end: datetime) -> list[Change]:
        out: list[Change] = []
        for rev_kind, owner_kinds in ROLLOUT_OWNERS.items():
            by_owner: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for obj in await self._k8s.list(rev_kind, namespace=namespace):
                owner = _owner(obj, owner_kinds)
                if owner:
                    by_owner.setdefault(owner, []).append(obj)
            for (kind, name), revs in by_owner.items():
                revs.sort(key=_revision)
                for i, rev in enumerate(revs):
                    created = _ts(rev["metadata"].get("creationTimestamp"))
                    if not created or not (start <= created <= end):
                        continue
                    before = _images(_template(revs[i - 1])) if i > 0 else None
                    after = _images(_template(rev))
                    out.append(
                        Change(
                            source="rollout",
                            kind=kind,
                            name=name,
                            namespace=namespace,
                            at=created,
                            details={
                                "revision": _revision(rev),
                                "images_before": before,
                                "images_after": after,
                                "image_changed": before is not None and before != after,
                            },
                        )
                    )
        return out

    @staticmethod
    def _managed_changes(
        kind: str, obj: dict[str, Any], start: datetime, end: datetime
    ) -> list[Change]:
        meta = obj["metadata"]
        labels = meta.get("labels") or {}
        annotations = meta.get("annotations") or {}
        out: list[Change] = []
        for entry in meta.get("managedFields") or []:
            if entry.get("subresource"):  # status vb. kontrolcü güncellemeleri
                continue
            source = classify_manager(entry.get("manager", ""))
            at = _ts(entry.get("time"))
            if source is None or at is None or not (start <= at <= end):
                continue
            details: dict[str, Any] = {
                "operation": entry.get("operation"),
                "fields": _changed_paths(entry.get("fieldsV1")),
            }
            if source == "helm":
                details["release"] = annotations.get("meta.helm.sh/release-name")
                details["chart"] = labels.get("helm.sh/chart")
                details["app_version"] = labels.get("app.kubernetes.io/version")
            out.append(
                Change(
                    source=source,
                    kind=kind,
                    name=meta["name"],
                    namespace=meta.get("namespace"),
                    at=at,
                    manager=entry.get("manager"),
                    details=details,
                )
            )
        return out
