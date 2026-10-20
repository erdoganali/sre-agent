from __future__ import annotations

from datetime import datetime
from typing import Any

from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.ports import Capability


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class PodRestarts(Check):
    """Alarmın namespace'inde, pencere içinde sonlanıp yeniden başlayan container'lar.

    Anomali: pencere içinde en az bir container yeniden başladı. Son sonlanma nedeni
    (OOMKilled, Error, ...) kanıta eklenir; OOMKilled Capacity'yi, Error Bug'ı işaret eder.
    """

    name = "pod_restarts"
    capability = Capability.K8S
    categories = ("capacity_performance", "application_service_bug")

    async def run(self, ctx: CheckContext) -> Evidence:
        namespace = ctx.labels.get("namespace")
        if not namespace:
            return self.evidence(ctx, Status.NO_DATA, "alarmda namespace etiketi yok")

        pods = await ctx.k8s().list("Pod", namespace=namespace)
        restarted: list[dict[str, Any]] = []
        for pod in pods:
            for cs in (pod.get("status") or {}).get("containerStatuses") or []:
                last = (cs.get("lastState") or {}).get("terminated") or {}
                finished = _ts(last.get("finishedAt"))
                if finished and ctx.window.start <= finished <= ctx.window.end:
                    restarted.append(
                        {
                            "pod": pod["metadata"]["name"],
                            "container": cs.get("name"),
                            "restartCount": cs.get("restartCount", 0),
                            "reason": last.get("reason"),
                            "exitCode": last.get("exitCode"),
                            "finishedAt": last.get("finishedAt"),
                        }
                    )

        query = f"list Pod namespace={namespace}"
        if not pods:
            return self.evidence(ctx, Status.NO_DATA, f"{namespace}: pod yok", query=query)
        if not restarted:
            return self.evidence(
                ctx, Status.OK, f"{namespace}: pencerede yeniden başlama yok", query=query
            )
        reasons = sorted({str(r["reason"]) for r in restarted})
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{namespace}: {len(restarted)} container yeniden başladı ({', '.join(reasons)})",
            data=restarted,
            query=query,
        )
