"""Alembic environment — async engine, autogenerate target from app.db.base.Base."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.base import Base
from app.db.models import *  # noqa: F401,F403 — register all models on Base.metadata

config = context.config
# Not DATABASE_URL: the app connects as an unprivileged role so RLS applies to
# it (migration 0006), and that role has no DDL rights by design. Falls back to
# DATABASE_URL when MIGRATION_DATABASE_URL is unset.
#
# '%' is doubled because set_main_option writes into a configparser, where '%'
# opens an interpolation. A password containing a percent-encoded character —
# '%40' for an '@', which the URL itself requires — otherwise dies with
# "invalid interpolation syntax" before a connection is ever attempted.
config.set_main_option("sqlalchemy.url", settings.alembic_database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
