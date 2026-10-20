"""Değişiklik kontrolleri: ChangePort'tan okur, kaynağa göre süzer."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, ClassVar

from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.ports import Capability, Change, ChangeSource


def _row(c: Change) -> dict[str, Any]:
    row = asdict(c)
    row["at"] = c.at.isoformat()
    return row


class _ChangeCheck(Check):
    _base = True
    source: ClassVar[ChangeSource]
    capability = Capability.CHANGE
    label: ClassVar[str]

    def describe(self, changes: list[Change]) -> str:
        names = sorted({f"{c.kind}/{c.name}" for c in changes})
        return f"{len(changes)} {self.label}: {', '.join(names[:5])}"

    async def run(self, ctx: CheckContext) -> Evidence:
        namespace = ctx.labels.get("namespace")
        if not namespace:
            return self.evidence(ctx, Status.NO_DATA, "alarmda namespace etiketi yok")
        port = ctx.changes()
        query = f"changes source={self.source} namespace={namespace}"
        if self.source not in port.sources:
            return self.evidence(
                ctx, Status.NO_DATA, f"{self.source} değişiklikleri bu ortamda görülemiyor"
            )
        found = [
            c
            for c in await port.changes(namespace, ctx.window.start, ctx.window.end)
            if c.source == self.source
        ]
        if not found:
            return self.evidence(
                ctx, Status.OK, f"{namespace}: pencerede {self.label} yok", query=query
            )
        return self.evidence(
            ctx, Status.ANOMALY, self.describe(found), data=[_row(c) for c in found], query=query
        )


class RecentRollouts(_ChangeCheck):
    """Pencerede yeni revizyona geçen Deployment/StatefulSet/DaemonSet'ler; image farkıyla."""

    name = "recent_rollouts"
    categories = ("application_release",)
    source = "rollout"
    label = "rollout"

    def describe(self, changes: list[Change]) -> str:
        imaged = [c for c in changes if c.details.get("image_changed")]
        base = super().describe(changes)
        return f"{base}; {len(imaged)} tanesinde image değişti" if imaged else base


class HelmReleaseChanges(_ChangeCheck):
    """Pencerede Helm'in güncellediği nesneler (release adıyla). Helm geçmişi okunmaz (ADR-019)."""

    name = "helm_release_changes"
    categories = ("application_release",)
    source = "helm"
    label = "Helm güncellemesi"

    def describe(self, changes: list[Change]) -> str:
        releases = sorted({str(c.details.get("release")) for c in changes})
        return f"{super().describe(changes)} (release: {', '.join(releases)})"


class ManualChanges(_ChangeCheck):
    """Pencerede kubectl ile elle değiştirilen nesneler (managedFields yöneticisi)."""

    name = "manual_changes"
    categories = ("human_error",)
    source = "manual"
    label = "elle değişiklik"

    def describe(self, changes: list[Change]) -> str:
        managers = sorted({str(c.manager) for c in changes})
        return f"{super().describe(changes)} ({', '.join(managers)})"


class ArgoCdSync(_ChangeCheck):
    """Pencerede Argo CD sync'i ya da OutOfSync drift'i."""

    name = "argocd_sync"
    categories = ("application_release",)
    source = "argocd"
    label = "Argo CD sync/drift"
