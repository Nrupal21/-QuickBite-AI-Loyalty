"""QuickBite — Base model with UUID PKs + created_at/updated_at.

All domain models inherit from this base. Provides:
- UUID primary key (auto-generated)
- created_at (server default)
- updated_at (auto-update on change)
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app.core.config import settings


def _engine_kwargs() -> dict:
    """Connection settings, adjusted when pointing at a transaction pooler.

    Supabase (and any PgBouncer in transaction mode) hands a different backend
    connection to each transaction, so a prepared statement created on one is
    gone by the next — asyncpg caches them by default and fails with
    `prepared statement "__asyncpg_stmt_x__" does not exist` under load rather
    than on the first call, which makes it look intermittent.

    `set_config('app.tenant_id', ..., true)` is transaction-local and therefore
    safe under transaction pooling: the tenant binding lives and dies inside
    the same transaction that does the reads. Session-scoped state would not be.
    """
    kwargs: dict = {"pool_pre_ping": True}
    if settings.DB_USE_TRANSACTION_POOLER:
        kwargs["connect_args"] = {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        }
        # The pooler owns pooling; a second pool underneath it just holds
        # server-side connections open that the pooler wants to recycle.
        kwargs["poolclass"] = NullPool
        kwargs.pop("pool_pre_ping")
    return kwargs


engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs())
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Declarative base — every domain model gets a UUID PK and timestamps."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session, closed after the request."""
    async with async_session_factory() as session:
        yield session
