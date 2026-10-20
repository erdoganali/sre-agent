"""Grafana anotasyonu: raporun özetini alarm başlangıcına işaretler.

Gözlem aracına yazma (cluster'a değil); Editor rollü servis hesabı token'ıyla (ADR-022).
"""

from __future__ import annotations

from datetime import datetime

import httpx


class GrafanaAnnotations:
    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=10.0, headers={"Authorization": f"Bearer {token}"}
        )

    async def annotate(self, at: datetime, text: str, tags: list[str]) -> int:
        resp = await self._client.post(
            "/api/annotations",
            json={"time": int(at.timestamp() * 1000), "text": text, "tags": tags},
        )
        resp.raise_for_status()
        return int(resp.json()["id"])

    async def aclose(self) -> None:
        await self._client.aclose()
