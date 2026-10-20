"""ChangePort'un Argo CD uygulaması (ADR-011); birim testiyle sınanır.

Argo CD Application kaynaklarından: pencere içindeki sync geçmişi (status.history) ve
hedef namespace'i bu olan uygulamaların drift durumu (status.sync.status = OutOfSync).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sre_agent.ports import Change, K8sPort


def _ts(value: Any) -> datetime | None:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None


class ArgoCdChangeSource:
    sources = frozenset({"argocd"})

    def __init__(self, k8s: K8sPort, argocd_namespace: str | None = None):
        self._k8s = k8s
        self._ns = argocd_namespace

    async def changes(self, namespace: str, start: datetime, end: datetime) -> list[Change]:
        out: list[Change] = []
        for app in await self._k8s.list_custom("argoproj.io", "Application", self._ns):
            dest = ((app.get("spec") or {}).get("destination") or {}).get("namespace")
            if dest != namespace:
                continue
            name = app["metadata"]["name"]
            status = app.get("status") or {}
            for h in status.get("history") or []:
                at = _ts(h.get("deployedAt"))
                if at and start <= at <= end:
                    out.append(
                        Change(
                            source="argocd",
                            kind="Application",
                            name=name,
                            namespace=namespace,
                            at=at,
                            manager="argocd",
                            details={"revision": h.get("revision"), "id": h.get("id")},
                        )
                    )
            sync = status.get("sync") or {}
            if sync.get("status") == "OutOfSync":
                out.append(
                    Change(
                        source="argocd",
                        kind="Application",
                        name=name,
                        namespace=namespace,
                        at=_ts(status.get("reconciledAt")) or end,
                        manager="argocd",
                        details={"drift": True, "revision": sync.get("revision")},
                    )
                )
        return out
