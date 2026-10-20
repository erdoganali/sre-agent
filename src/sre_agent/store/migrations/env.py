"""Alembic ortamı (async, asyncpg). Sürüm tablosu `reports` şemasında tutulur."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from sre_agent.store.postgres import SCHEMA, metadata


def _run(connection: Connection) -> None:
    # Sürüm tablosu bu şemada; ilk kurulumda migration'dan önce var olmalı.
    connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    context.configure(
        connection=connection,
        target_metadata=metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(context.config.get_main_option("sqlalchemy.url") or "")
    async with engine.connect() as conn:
        await conn.run_sync(_run)
        await conn.commit()
    await engine.dispose()


asyncio.run(_main())
