"""Şema migration'ları (Alembic). Servis açılırken `upgrade` çalışır (tek replika)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from sre_agent.store.postgres import async_url

MIGRATIONS = Path(__file__).parent / "migrations"


def config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", async_url(url).replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(config(url), revision)


async def upgrade_async(url: str) -> None:
    # Alembic'in env.py'si kendi event loop'unu açar; ayrı thread'de çalıştırılır.
    await asyncio.to_thread(upgrade, url)
