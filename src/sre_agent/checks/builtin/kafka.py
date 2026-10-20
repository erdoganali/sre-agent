from __future__ import annotations

from typing import Any

from sre_agent.checks import Check, CheckContext, Evidence, Status
from sre_agent.ports import Capability

LAG = "sum by (consumergroup, topic) (kafka_consumergroup_lag)"
BASELINE = "sum by (consumergroup, topic) (avg_over_time(kafka_consumergroup_lag[1d]))"


class KafkaConsumerLag(Check):
    """Consumer lag'i günlük ortalamasıyla (baseline) karşılaştırır.

    Anomali (plan: "Kafka lag baseline'ın 2 katı"): lag > FACTOR x baseline ve lag > MIN_MESSAGES.
    Alt sınır, baseline ~0 iken tek bir mesajın anomali sayılmasını engeller.
    """

    name = "kafka_consumer_lag"
    capability = Capability.METRICS
    categories = ("infrastructure_system",)
    FACTOR = 2.0
    MIN_MESSAGES = 100.0

    async def run(self, ctx: CheckContext) -> Evidence:
        metrics = ctx.metrics()
        at = ctx.window.end
        lag = await metrics.query(LAG, at)
        query = f"{LAG}\nbaseline: {BASELINE}"
        if not lag:
            return self.evidence(ctx, Status.NO_DATA, "kafka_consumergroup_lag yok", query=query)

        baseline = {
            (s.labels.get("consumergroup"), s.labels.get("topic")): s.value
            for s in await metrics.query(BASELINE, at)
        }
        rows: list[dict[str, Any]] = []
        for s in lag:
            key = (s.labels.get("consumergroup"), s.labels.get("topic"))
            base = baseline.get(key, 0.0)
            rows.append(
                {
                    "consumergroup": key[0],
                    "topic": key[1],
                    "lag": s.value,
                    "baseline": round(base, 2),
                    "anomaly": s.value > self.FACTOR * base and s.value > self.MIN_MESSAGES,
                }
            )
        bad = [r for r in rows if r["anomaly"]]
        if not bad:
            return self.evidence(
                ctx, Status.OK, f"{len(rows)} grup/topic lag normal", data=rows, query=query
            )
        worst = max(bad, key=lambda r: float(r["lag"]))
        return self.evidence(
            ctx,
            Status.ANOMALY,
            f"{worst['consumergroup']}/{worst['topic']} lag {worst['lag']:.0f} "
            f"(baseline {worst['baseline']}); {len(bad)} grup/topic baseline'ın "
            f"{self.FACTOR:g} katının üzerinde",
            data=rows,
            query=query,
        )
