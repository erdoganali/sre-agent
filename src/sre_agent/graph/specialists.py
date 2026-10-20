"""Specialist'ler. T3.2'de modelsiz: Faz 1'in kendi yeteneğine düşen kanıtını tipli bulguya çevirir.
T3.3 ve T3.4'te aynı arayüzün arkasına MCP araçlarıyla çalışan model specialist'i gelir."""

from __future__ import annotations

from collections import Counter
from typing import Any, Protocol

from sre_agent.graph.schemas import EvidenceRef, Finding


class Specialist(Protocol):
    async def run(
        self, name: str, brief: str, evidence: list[dict[str, Any]], question: str | None
    ) -> Finding: ...


class EvidenceSpecialist:
    """Faz 1 kanıtını özetler: hangi kontroller anomali gördü, hangi kategorilere işaret ediyor."""

    async def run(
        self, name: str, brief: str, evidence: list[dict[str, Any]], question: str | None
    ) -> Finding:
        votes: Counter[str] = Counter(c for e in evidence for c in e.get("categories", []))
        n = len({e["check"] for e in evidence})
        confidence = "high" if n >= 2 else "medium" if n == 1 else "low"
        observations = [f"{e['check']}: {e['summary']}" for e in evidence[:10]]
        if question:
            observations.append(
                f"deep dive sorusu: {question} (kanıt toplayan specialist T3.4'te; "
                "şimdilik yalnız Faz 1 kanıtı)"
            )
        return Finding(
            specialist=name,
            summary=(
                f"{n} kontrol anomali gördü: {', '.join(sorted({e['check'] for e in evidence}))}"
                if n
                else "bu yetenekte anomali yok"
            ),
            observations=observations,
            evidence=[
                EvidenceRef(source=e["check"], summary=e["summary"][:300]) for e in evidence[:10]
            ],
            suspects=[c for c, _ in votes.most_common(3)],
            confidence=confidence,  # type: ignore[arg-type]
        )
