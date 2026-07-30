"""QuickBite — Track + check plan limits.

A deliberately narrow slice of SUB-02. REVIEW-01's criteria require that a
successful AI draft increments the counter and a total provider failure does
not, so the increment has to exist. Everything else SUB-02 owns —
`check_plan_limit()`, 402 responses, enforcement against
`SubscriptionPlan.feature_limits` — is **not** implemented here. Nothing in
this module blocks a request; it only records.

`usage_tracking` is RLS-protected and lives in the isolated `payment` schema,
so callers must have bound tenant context (`app.tenant_id`) before calling in.
Without it the SELECT half of the upsert silently sees zero rows and every
month looks like the tenant's first.
"""

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.subscription import UsageTracking

logger = structlog.get_logger(__name__)


async def increment_ai_usage(
    session: AsyncSession, tenant_id: uuid.UUID, *, amount: int = 1
) -> int:
    """Add `amount` to this tenant's AI counter for the current month.

    Returns the new total. Does **not** commit — the caller owns the
    transaction, so the counter moves in the same unit of work as the thing it
    counts and can never be left incremented by a request that later rolls back.

    Atomic by construction: a single `INSERT ... ON CONFLICT DO UPDATE` where
    the new value is computed *in the database* from the stored one. The
    obvious SELECT-then-add version loses increments whenever two drafts for
    one tenant overlap — both read N, both write N+1, and one generation is
    given away. Harmless while this only reports, but SUB-02 will gate paid
    quota on this number, and a counter that silently undercounts under load is
    a counter that hands out free capacity exactly when the tenant is busiest.

    `ON CONFLICT` targets the `uq_usage_tenant_period` constraint, so the
    insert and the update are one statement with no window between them — no
    row-exists check to race, and no IntegrityError to catch when two requests
    create the first row of a month simultaneously.

    The period is UTC, matching `created_at`'s server default. Deriving it from
    server-local time would double-count or skip an increment at every month
    boundary for a tenant in Asia/Kolkata.
    """
    now = datetime.now(timezone.utc)

    statement = insert(UsageTracking).values(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        period_year=now.year,
        period_month=now.month,
        ai_responses_used=amount,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_usage_tenant_period",
        # Reads the *stored* column, not a value this process loaded earlier,
        # so concurrent callers serialise on the row lock instead of racing.
        set_={"ai_responses_used": UsageTracking.ai_responses_used + amount},
    ).returning(UsageTracking.ai_responses_used)

    result = await session.execute(statement)
    new_total = result.scalar_one()

    logger.info("usage.ai_responses.incremented", tenant_id=str(tenant_id), total=new_total)
    return new_total
