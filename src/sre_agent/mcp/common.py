"""Araçların ortak parçaları: zaman penceresi, Markdown tablo, sunucu tarafı üst sınır.

Cevaplar JSON yerine Markdown: model için daha az token, daha kolay okunur (plan, "Token
bütçesi"). Asıl kırpma (~8K token) istemcide bütçeyle yapılır (client.py); burada yalnız kaba bir
üst sınır var.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
SERVER_MAX_CHARS = 40_000  # ~10K token; istemci zaten 8K'ya indirir
MAX_ROWS = 50


K8S_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")


class ToolInputError(ToolError):
    """Araç argümanı geçersiz (zaman, sınır); mesaj modele gider, düzeltip yeniden çağırabilir."""


def guarded[**P](fn: Callable[P, Awaitable[str]]) -> Callable[P, Awaitable[str]]:
    """Arka uç hatasını (geçersiz sorgu, HTTP 4xx/5xx, erişim) modelin okuyacağı kısa mesaja
    çevirir. SDK beklenmeyen hatanın metnini gizler; sorguyu düzeltmek için mesaj gerekir."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            raise ToolError(f"arka uç HTTP {exc.response.status_code}: {body}") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"arka uca erişilemedi: {type(exc).__name__}") from exc
        except (RuntimeError, ValueError, KeyError) as exc:
            raise ToolError(f"{type(exc).__name__}: {str(exc)[:300]}") from exc

    return wrapper


def selector_value(v: str, what: str) -> str:
    """PromQL/LogQL seçicisine konacak değer: tırnak, ters bölü, süslü parantez, satır sonu
    kabul edilmez (sorguya enjeksiyon)."""
    if any(c in v for c in '"\\\n`{}'):
        raise ToolInputError(f"{what} içinde tırnak, ters bölü, süslü parantez olamaz")
    return v


def window(end: str | None, minutes: int, max_minutes: int) -> tuple[datetime, datetime]:
    """`end` ISO 8601 (boşsa şimdi) ve geriye `minutes` dakika; pencere sınırlıdır."""
    if not 1 <= minutes <= max_minutes:
        raise ToolInputError(f"minutes 1..{max_minutes} olmalı (verilen {minutes})")
    if end:
        try:
            stop = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolInputError(
                f"end ISO 8601 olmalı, ör. 2026-10-09T16:56:00Z ({end!r})"
            ) from exc
        stop = stop if stop.tzinfo else stop.replace(tzinfo=UTC)
    else:
        stop = datetime.now(UTC)
    return stop - timedelta(minutes=minutes), stop


def at_time(at: str | None) -> datetime:
    start, _ = window(at, 1, 1)
    return start + timedelta(minutes=1)


def ts(dt: datetime | None) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ") if dt else "-"


def num(v: float | None) -> str:
    if v is None:
        return "-"
    if v != v:  # NaN
        return "NaN"
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.2f}G"
    if a >= 1e6:
        return f"{v / 1e6:.2f}M"
    if a >= 1e4:
        return f"{v / 1e3:.1f}K"
    if a == int(a):
        return str(int(v))
    if a >= 100:
        return f"{v:.0f}"
    return f"{v:.3g}"


def _cell(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], max_rows: int = MAX_ROWS) -> str:
    """Markdown tablo; satır sınırını aşan kısım bir satırla belirtilir."""
    rows = list(rows)
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows[:max_rows]]
    if len(rows) > max_rows:
        out.append(f"\n({len(rows) - max_rows} satır daha var; sorguyu daralt)")
    return "\n".join(out)


def labels_text(labels: dict[str, str], drop: Iterable[str] = ()) -> str:
    skip = set(drop)
    return ", ".join(f"{k}={v}" for k, v in sorted(labels.items()) if k not in skip) or "-"


def cap(text: str, limit: int = SERVER_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n… (sunucu {len(text) - limit} karakteri kesti; sorguyu daralt)"
