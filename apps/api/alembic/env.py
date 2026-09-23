"""Alembic environment (async, asyncpg). The URL comes from app settings."""

import asyncio
from logging.config import fileConfig
from typing import Literal

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.core.crypto import EncryptedString
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# A URL passed programmatically (e.g. by tests) wins over settings.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", str(get_settings().database_url))

target_metadata = Base.metadata


def _render_item(type_: str, obj: object, autogen_context: object) -> str | Literal[False]:
    # Migrations must not depend on application types: encrypted columns are plain TEXT
    # in the database (encryption happens in the app).
    if type_ == "type" and isinstance(obj, EncryptedString):
        return "sa.Text()"
    return False


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_item=_render_item,
        **kwargs,  # type: ignore[arg-type]
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of connecting (``alembic upgrade head --sql``)."""
    _configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    existing = config.attributes.get("connection")
    if existing is not None:  # tests hand over a live sync connection
        do_run_migrations(existing)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
