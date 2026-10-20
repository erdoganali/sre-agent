from __future__ import annotations

from typing import Any

import httpx


class HttpReader:
    """Yalnızca GET yapan ortak HTTP istemcisi."""

    def __init__(
        self, base_url: str, timeout_s: float = 15.0, client: httpx.AsyncClient | None = None
    ):
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def get_json(self, path: str, params: dict[str, Any]) -> Any:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()
