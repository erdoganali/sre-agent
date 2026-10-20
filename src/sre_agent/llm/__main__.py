"""Model kataloğundaki rolleri gerçek uç noktaya karşı sınar (T2.1 kabulü).

  python -m sre_agent.llm check [--catalog config/models.yaml] [--role decision ...]

Her rol için: düz cevap, tipli cevap (structured output ya da yedek yol) ve sınıflandırma
(logprob ya da yedek). Model içeriği yazdırılmaz; yalnızca süre, token ve yetenekler.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from opentelemetry import trace
from pydantic import BaseModel

from sre_agent.adapters.openai_compat import OpenAICompatModel
from sre_agent.llm.budget import Budget, BudgetedModel
from sre_agent.llm.catalog import load_catalog
from sre_agent.llm.telemetry import InstrumentedModel, setup_tracing
from sre_agent.ports.model import Message, ModelError


class _Probe(BaseModel):
    category: str
    confidence: float


async def check(catalog_path: Path, roles: list[str]) -> int:
    catalog = load_catalog(catalog_path)
    tracing = setup_tracing("sre-agent-model-check")
    adapter = OpenAICompatModel(catalog)
    budget = Budget()
    # Servisteki zincirle aynı: telemetri(bütçe(adaptör))
    model = InstrumentedModel(BudgetedModel(adapter, catalog, budget), catalog)
    failed = 0
    try:
        for role in roles or list(catalog.roles):
            key, spec, _ = catalog.resolve(role)
            print(f"## {role} → {key} ({spec.url})")
            ask = [Message("user", "Kubernetes'te OOMKilled ne demek? Tek cümle.")]
            try:
                c = await model.complete(role, ask, max_tokens=200)
                print(
                    f"  cevap      OK  {c.latency_s:.1f} sn, token {c.usage.prompt_tokens}+"
                    f"{c.usage.completion_tokens}, düşünme {'var' if c.reasoning else 'yok'}"
                )
                t = await model.complete_typed(
                    role,
                    [
                        Message(
                            "user",
                            "Bir pod OOMKilled oldu. Kategori (capacity/network/...) ve güven?",
                        )
                    ],
                    _Probe,
                    max_tokens=300,
                )
                print(f"  tipli      OK  {t.completion.latency_s:.1f} sn, düzeltme: {t.repaired}")
                k = await model.classify(
                    role,
                    [Message("user", "Alarm: container OOMKilled. Hangi kategori?")],
                    ["capacity", "network", "security"],
                )
                label, p = k.top
                print(f"  sınıflama  OK  {k.method}, en olası {label} ({p:.2f})")
            except ModelError as exc:
                failed += 1
                print(f"  FAIL {exc}")
            print(f"  yetenekler {adapter.capabilities.get(key, {})}")
        print(f"bütçe: {budget.summary()}")
    finally:
        await adapter.aclose()
        if tracing:
            trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]
            print("span'lar Tempo'ya gönderildi (gen_ai.operation.name=chat)")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="sre_agent.llm")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="rolleri gerçek uç noktaya karşı sına")
    c.add_argument("--catalog", type=Path, default=Path("config/models.yaml"))
    c.add_argument("--role", action="append", default=[])
    args = parser.parse_args()
    return asyncio.run(check(args.catalog, args.role))


if __name__ == "__main__":
    sys.exit(main())
