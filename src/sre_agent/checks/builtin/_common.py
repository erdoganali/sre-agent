"""Kontroller arası ortak yardımcılar."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sre_agent.checks import CheckContext
from sre_agent.ports import Sample

GIB = 1024**3


def namespace(ctx: CheckContext) -> str | None:
    return ctx.labels.get("namespace") or None


def prom_range(ctx: CheckContext) -> str:
    """Pencere uzunluğu PromQL aralığı olarak (ör. 1800s); en az 60 sn."""
    seconds = int((ctx.window.end - ctx.window.start).total_seconds())
    return f"{max(seconds, 60)}s"


def ts(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def pct(ratio: float) -> str:
    return f"%{ratio * 100:.0f}"


def over(samples: list[Sample], threshold: float) -> list[dict[str, Any]]:
    """Eşiği aşan örnekler: etiketler + ratio, en yüksekten düşüğe."""
    rows: list[dict[str, Any]] = [
        {**s.labels, "ratio": round(s.value, 3)} for s in samples if s.value > threshold
    ]
    return sorted(rows, key=lambda r: -float(r["ratio"]))
