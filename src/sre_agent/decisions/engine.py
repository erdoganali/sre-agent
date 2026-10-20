"""Karar motoru: kural ve model kararlarını üretir, moda göre hangisinin etkili olduğunu seçer."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sre_agent.decisions import (
    NOT_ASSESSABLE,
    UNKNOWN,
    Decision,
    DecisionConfig,
    DecisionSet,
    Mode,
    Point,
    brief,
    decide,
    input_hash,
    rules,
)
from sre_agent.ports.model import Classification, ModelPort
from sre_agent.report import Report
from sre_agent.taxonomy import Taxonomy

log = logging.getLogger(__name__)
ROLE = "decision"


class ModelUnusable(Exception):
    """Model cevap verdi ama karar için kullanılamaz (ör. logprob yok)."""


class DecisionEngine:
    def __init__(self, cfg: DecisionConfig, taxonomy: Taxonomy, model: ModelPort | None):
        self.cfg = cfg
        self.th = cfg.thresholds
        self.taxonomy = taxonomy
        self.model = model if cfg.mode is not Mode.OFF else None

    async def assess(self, report: Report, history: Iterable[dict[str, Any]] = ()) -> DecisionSet:
        s = brief.summary(report, self.cfg.max_example_lines, self.cfg.max_example_chars)
        model_rules = self.cfg.mode is Mode.MODEL and self.model is not None
        out: list[Decision] = [rules.known(report, history, self.th)]
        cov = rules.coverage(report)

        if cov < self.th.min_coverage:
            # Yarım kanıtla tahmin yok: model çağrılmaz (ADR-028)
            out.append(rules.not_assessable(cov, self.th, True, "rules"))
            return self._set(s, out, noise_flag=False)

        rule_noise = rules.noise(report, self.th, effective=True)
        rule_cls = rules.classify(report, self.taxonomy, self.th, effective=True)
        model_noise: Decision | None = None
        model_cls: list[Decision] = []
        if self.model is not None:
            model_noise = await self._model_noise(s)
            model_cls = await self._model_classify(s)

        model_ok = model_rules and all(d.error is None for d in [model_noise, *model_cls] if d)
        for d in [rule_noise, *rule_cls]:
            d.effective = not model_ok
        for d in [d for d in [model_noise, *model_cls] if d]:
            d.effective = model_ok
        out += [rule_noise, *rule_cls]
        out += [d for d in [model_noise, *model_cls] if d]

        noise = model_noise if model_ok and model_noise else rule_noise
        flag = noise.outcome == "gürültü" and (noise.probability or 0) >= self.th.noise_flag
        return self._set(s, out, noise_flag=flag)

    def _set(self, s: dict[str, Any], out: list[Decision], noise_flag: bool) -> DecisionSet:
        return DecisionSet(
            mode=self.cfg.mode,
            taxonomy_version=self.taxonomy.version,
            input_hash=input_hash(s),
            thresholds=self.th,
            decisions=out,
            noise_flag=noise_flag,
        )

    async def _ask(self, s: dict[str, Any], question: str, labels: list[str]) -> Classification:
        assert self.model is not None
        c = await self.model.classify(ROLE, brief.messages(s, question), labels)
        if c.method != "logprobs":
            # Metinle yazılmış güven sayısı kalibre değildir; karar için kullanılmaz (ADR-028)
            raise ModelUnusable("sunucu logprob vermedi; model kararı kullanılamaz")
        return c

    def _failed(self, point: Point, exc: Exception) -> Decision:
        log.warning("model kararı alınamadı (%s): %s", point, exc)
        return Decision(
            point=point,
            source="model",
            effective=False,
            outcome=UNKNOWN,
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
            method="logprobs",
        )

    async def _model_noise(self, s: dict[str, Any]) -> Decision:
        q = (
            "Bu alarm gerçek bir olay mı, yoksa kendiliğinden geçen / anlamsız bir gürültü mü? "
            "(olay: incelenmeli; gürültü: kanıt bir soruna işaret etmiyor)"
        )
        try:
            c = await self._ask(s, q, list(rules.NOISE_LABELS))
        except Exception as exc:
            return self._failed(Point.NOISE, exc)
        out, top, margin = decide(c.probs, self.th)
        return Decision(
            point=Point.NOISE,
            source="model",
            effective=False,
            outcome=out,
            probability=top,
            probs=c.probs,
            margin=margin,
            model=c.completion.model,
            method=c.method,
        )

    async def _model_classify(self, s: dict[str, Any]) -> list[Decision]:
        groups = {f"{g.id} ({g.name})": g.id for g in self.taxonomy.groups}
        q = "Kök neden hangi gruba giriyor?"
        try:
            c = await self._ask(s, q, list(groups))
        except Exception as exc:
            return [self._failed(Point.GROUP, exc)]
        gprobs = {groups[k]: v for k, v in c.probs.items()}
        g_out, g_top, g_margin = decide(gprobs, self.th)
        res = [
            Decision(
                point=Point.GROUP,
                source="model",
                effective=False,
                outcome=g_out,
                probability=g_top,
                probs=gprobs,
                margin=g_margin,
                model=c.completion.model,
                method=c.method,
                reason="" if g_out != UNKNOWN else "grup belirsiz: kategori sorulmadı",
            )
        ]
        if g_out in (UNKNOWN, NOT_ASSESSABLE):
            return res
        cats = [cat.id for cat in self.taxonomy.in_group(g_out)]
        if len(cats) == 1:
            res.append(
                Decision(
                    point=Point.CATEGORY,
                    source="model",
                    effective=False,
                    outcome=cats[0],
                    probability=g_top,
                    probs={cats[0]: 1.0},
                    margin=1.0,
                    model=c.completion.model,
                    method="single",
                    reason="grupta tek kategori",
                )
            )
            return res
        q = (
            "Kök neden bu gruptaki hangi kategori? Tanımlar ve ayırt etme kuralları:\n"
            + brief.category_guide(self.taxonomy, g_out)
        )
        try:
            k = await self._ask(s, q, cats)
        except Exception as exc:
            res.append(self._failed(Point.CATEGORY, exc))
            return res
        c_out, c_top, c_margin = decide(k.probs, self.th)
        res.append(
            Decision(
                point=Point.CATEGORY,
                source="model",
                effective=False,
                outcome=c_out,
                probability=round(g_top * c_top, 4),  # nihai: grup x grup içi
                probs=k.probs,
                margin=c_margin,
                model=k.completion.model,
                method=k.method,
            )
        )
        return res
