"""Unit tests for usage_service — the atomic monthly counter.

What matters here is the *shape* of the statement, not just its result. A
SELECT-then-add version passes any single-threaded assertion about the returned
total while still losing increments under concurrency, so these tests pin the
properties that make the race impossible: one round trip, an ON CONFLICT
upsert, and an increment computed from the stored column rather than from a
value this process read earlier.

Concurrency itself is proven against a real database rather than here — a
mocked session cannot race with itself.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import usage_service

TENANT_ID = uuid.uuid4()


def make_session(returned_total: int = 1) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = returned_total
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


def compiled_sql(session: MagicMock) -> str:
    statement = session.execute.await_args.args[0]
    return str(statement.compile(compile_kwargs={"literal_binds": False}))


@pytest.mark.asyncio
async def test_returns_the_new_total_from_the_database():
    session = make_session(returned_total=7)

    total = await usage_service.increment_ai_usage(session, TENANT_ID)

    assert total == 7


@pytest.mark.asyncio
async def test_uses_a_single_round_trip():
    """Two statements means a window between them, and a window is the race."""
    session = make_session()

    await usage_service.increment_ai_usage(session, TENANT_ID)

    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_statement_is_an_upsert_on_the_period_constraint():
    """ON CONFLICT is what removes the row-exists check that two concurrent
    first-of-the-month requests would otherwise both lose."""
    session = make_session()

    await usage_service.increment_ai_usage(session, TENANT_ID)
    sql = compiled_sql(session).lower()

    assert "insert into payment.usage_tracking" in sql
    assert "on conflict on constraint uq_usage_tenant_period" in sql
    assert "do update set" in sql


@pytest.mark.asyncio
async def test_increment_reads_the_stored_column_not_a_preloaded_value():
    """The defining property: `ai_responses_used = ai_responses_used + N` is
    evaluated by Postgres under a row lock. A Python-side `row.x += n` computes
    from a value read earlier, so two overlapping requests both write N+1."""
    session = make_session()

    await usage_service.increment_ai_usage(session, TENANT_ID)
    sql = compiled_sql(session).lower()

    assert "usage_tracking.ai_responses_used +" in sql


@pytest.mark.asyncio
async def test_never_issues_a_select():
    """A SELECT before the write is exactly the read-modify-write this replaced."""
    session = make_session()

    await usage_service.increment_ai_usage(session, TENANT_ID)

    assert "select" not in compiled_sql(session).lower().split("returning")[0]


@pytest.mark.asyncio
async def test_returns_the_updated_value_via_returning():
    session = make_session()

    await usage_service.increment_ai_usage(session, TENANT_ID)

    assert "returning" in compiled_sql(session).lower()


@pytest.mark.asyncio
async def test_does_not_commit_so_the_caller_owns_the_transaction():
    """The counter must move in the same unit of work as the thing it counts,
    or a later rollback leaves a tenant billed for a draft they never got."""
    session = make_session()

    await usage_service.increment_ai_usage(session, TENANT_ID)

    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_period_is_utc_not_server_local():
    """Server-local time double-counts or skips at every month boundary for a
    tenant in Asia/Kolkata, whose month turns over 5.5 hours before UTC's."""
    session = make_session()
    now = datetime.now(timezone.utc)

    await usage_service.increment_ai_usage(session, TENANT_ID)

    params = session.execute.await_args.args[0].compile().params
    assert params["period_year"] == now.year
    assert params["period_month"] == now.month


@pytest.mark.asyncio
async def test_custom_amount_is_applied():
    session = make_session(returned_total=5)

    total = await usage_service.increment_ai_usage(session, TENANT_ID, amount=5)

    assert total == 5
    assert session.execute.await_args.args[0].compile().params["ai_responses_used"] == 5
