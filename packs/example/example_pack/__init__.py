"""Example pack module: exports `PACK` (checks, golden-signal signals, readable custom kinds)."""

from typing import ClassVar

from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.packs import PackContent
from sre_agent.ports import Capability

ERROR_RATIO_MAX = 0.05


class ExampleErrorRate(Check):
    """5xx share of orders-api requests in the alert's namespace."""

    name = "example_error_rate"
    capability = Capability.METRICS
    categories = ("application_service_bug",)
    queries: ClassVar[dict[str, str]] = {
        "ratio": (
            'sum(rate(http_requests_total{{namespace="{ns}",code=~"5.."}}[5m]))'
            ' / sum(rate(http_requests_total{{namespace="{ns}"}}[5m]))'
        )
    }

    async def run(self, ctx: CheckContext) -> Evidence:
        ns = ctx.labels.get("namespace", "")
        query = self.q["ratio"].format(ns=ns)
        samples = await ctx.metrics().query(query, ctx.window.end)
        if not samples:
            return self.evidence(ctx, Status.NO_DATA, f"{ns}: no request metrics", query=query)
        ratio = samples[0].value
        status = Status.ANOMALY if ratio > ERROR_RATIO_MAX else Status.OK
        return self.evidence(ctx, status, f"{ns}: 5xx ratio {ratio:.1%}", query=query)


PACK = PackContent(checks=(ExampleErrorRate,))
