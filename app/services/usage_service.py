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
from sqlalchemy import select
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

    The period is UTC, matching `created_at`'s server default. Deriving it from
    server-local time would double-count or skip an increment at every month
    boundary for a tenant in Asia/Kolkata.
    """
    now = datetime.now(timezone.utc)

    result = await session.execute(
        select(UsageTracking).where(
            UsageTracking.tenant_id == tenant_id,
            UsageTracking.period_year == now.year,
            UsageTracking.period_month == now.month,
        )
    )
    row = result.scalar_one_or_none()

    if row is None:
        row = UsageTracking(
            tenant_id=tenant_id,
            period_year=now.year,
            period_month=now.month,
            ai_responses_used=amount,
        )
        session.add(row)
        new_total = amount
    else:
        # TODO(SUB-02): this read-modify-write can undercount under concurrent
        # drafts. Acceptable while the counter only reports; it must become an
        # atomic `UPDATE ... SET x = x + 1` before it gates anything, since a
        # lost increment there hands out free quota.
        row.ai_responses_used += amount
        new_total = row.ai_responses_used

    logger.info("usage.ai_responses.incremented", tenant_id=str(tenant_id), total=new_total)
    return new_total
