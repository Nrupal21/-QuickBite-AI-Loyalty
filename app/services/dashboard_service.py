"""QuickBite — Dashboard stats (DASH-01) + loyalty analytics (DASH-02).

Every query here relies on the tenant context `get_current_user` already
bound on `session` (see `resolve_principal` in
`app/api/v1/dependencies/auth.py`) — none of them add an explicit
`tenant_id` filter, the same way `response_service.py` documents for its own
RLS-scoped queries.

Review-derived fields (`avg_rating`, `review_count`, `sentiment_trend`,
`pending_approvals`) read `restaurant.customer_reviews` /
`restaurant.review_responses`, and will read back empty until REVIEW-01
(customer review submission) or REVIEW-03 (GMB inbound sync) actually write
rows there — neither exists yet. `loyalty_scans_today` and
`fraud_alert_count` read `customer.stamp_logs`, which LOYALTY-03/04 already
populate, so those two are backed by real data today.

"Today" is a UTC calendar day, not the tenant's local day (`Tenant.timezone`
exists but isn't consulted here) — a reasonable v1 simplification, not a
correctness guarantee for a tenant far from UTC.

DASH-02's fraud log always reports `reason="Outside geofence radius"`:
`is_fraudulent` is set exactly once today, in `LoyaltyService.process_scan`'s
geofence check (`loyalty_service.py`'s module docstring — "fraud-pattern
detection beyond the geofence check... is not implemented yet"). A real
`reason` column belongs to SEC-12/13, once there is more than one possible
cause to distinguish.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_pii
from app.db.models.branch import Branch
from app.db.models.customer import Customer
from app.db.models.loyalty import RewardRedemption, StampLog
from app.db.models.reputation import CustomerReview, ReviewResponse
from app.schemas.dashboard import (
    BranchComparisonPoint,
    DashboardStatsResponse,
    FraudLogEntry,
    HeatmapPoint,
    LoyaltyAnalyticsResponse,
    SentimentTrendPoint,
    TopCustomer,
)

SENTIMENT_TREND_DAYS = 7
ANALYTICS_WINDOW_DAYS = 30
TOP_CUSTOMERS_LIMIT = 10
FRAUD_LOG_LIMIT = 100
_ONLY_FRAUD_REASON = "Outside geofence radius"


def _start_of_utc_day(when: datetime) -> datetime:
    return when.replace(hour=0, minute=0, second=0, microsecond=0)


async def get_stats(session: AsyncSession, *, can_approve: bool) -> DashboardStatsResponse:
    now = datetime.now(UTC)
    today_start = _start_of_utc_day(now)
    trend_start = today_start - timedelta(days=SENTIMENT_TREND_DAYS - 1)

    avg_rating_result = await session.execute(select(func.avg(CustomerReview.rating)))
    avg_rating = avg_rating_result.scalar_one_or_none()

    review_count_result = await session.execute(select(func.count(CustomerReview.id)))
    review_count = review_count_result.scalar_one()

    pending_result = await session.execute(
        select(func.count(ReviewResponse.id)).where(ReviewResponse.approval_state == "pending")
    )
    pending_approvals = pending_result.scalar_one()

    scans_today_result = await session.execute(
        select(func.count(StampLog.id)).where(
            StampLog.scanned_at >= today_start,
            StampLog.is_fraudulent.is_(False),
        )
    )
    loyalty_scans_today = scans_today_result.scalar_one()

    fraud_today_result = await session.execute(
        select(func.count(StampLog.id)).where(
            StampLog.scanned_at >= today_start,
            StampLog.is_fraudulent.is_(True),
        )
    )
    fraud_alert_count = fraud_today_result.scalar_one()

    sentiment_trend = await _sentiment_trend(session, trend_start)

    return DashboardStatsResponse(
        avg_rating=round(avg_rating, 2) if avg_rating is not None else None,
        review_count=review_count,
        sentiment_trend=sentiment_trend,
        pending_approvals=pending_approvals,
        loyalty_scans_today=loyalty_scans_today,
        fraud_alert_count=fraud_alert_count,
        can_approve=can_approve,
    )


async def _sentiment_trend(
    session: AsyncSession, trend_start: datetime
) -> list[SentimentTrendPoint]:
    day = func.date_trunc("day", CustomerReview.reviewed_at)
    result = await session.execute(
        select(day.label("day"), func.avg(CustomerReview.sentiment_score))
        .where(CustomerReview.reviewed_at >= trend_start)
        .group_by(day)
    )
    by_day = {row[0].date().isoformat(): row[1] for row in result.all()}

    points: list[SentimentTrendPoint] = []
    for offset in range(SENTIMENT_TREND_DAYS):
        date = (trend_start + timedelta(days=offset)).date().isoformat()
        avg = by_day.get(date)
        points.append(
            SentimentTrendPoint(date=date, avg_sentiment=round(avg, 3) if avg is not None else None)
        )
    return points


def _mask_phone(phone: str) -> str:
    """`*** **** XXXX` — DASH-02's acceptance criterion, last 4 digits only.
    Every stored phone has a country code + at least 4 local digits, so
    `phone[-4:]` never crosses into the country code."""
    return f"*** **** {phone[-4:]}"


async def get_loyalty_analytics(
    session: AsyncSession, *, branch_id: uuid.UUID | None
) -> LoyaltyAnalyticsResponse:
    """`branch_id` filters the heatmap, branch comparison, and fraud log —
    the three views that are inherently per-branch. Top customers and the
    redemption rate stay tenant-wide: a customer isn't tied to one branch
    (no `branch_id` column on `Customer`), and neither is a redemption rate
    meaningful sliced any finer than the ticket asks for."""
    window_start = datetime.now(UTC) - timedelta(days=ANALYTICS_WINDOW_DAYS)

    total_stamps_month = await _total_stamps_month(session, window_start, branch_id)
    active_loyalty_customers = await _active_loyalty_customers(session, window_start, branch_id)
    heatmap = await _heatmap(session, window_start, branch_id)
    top_customers = await _top_customers(session)
    branch_comparison = await _branch_comparison(session, window_start, branch_id)
    redemption_rate = await _redemption_rate(session)
    fraud_log = await _fraud_log(session, branch_id)

    return LoyaltyAnalyticsResponse(
        total_stamps_month=total_stamps_month,
        active_loyalty_customers=active_loyalty_customers,
        heatmap=heatmap,
        top_customers=top_customers,
        branch_comparison=branch_comparison,
        redemption_rate=redemption_rate,
        fraud_log=fraud_log,
    )


async def _total_stamps_month(
    session: AsyncSession, window_start: datetime, branch_id: uuid.UUID | None
) -> int:
    query = select(func.count(StampLog.id)).where(
        StampLog.scanned_at >= window_start, StampLog.is_fraudulent.is_(False)
    )
    if branch_id is not None:
        query = query.where(StampLog.branch_id == branch_id)
    result = await session.execute(query)
    return result.scalar_one()


async def _active_loyalty_customers(
    session: AsyncSession, window_start: datetime, branch_id: uuid.UUID | None
) -> int:
    query = select(func.count(func.distinct(StampLog.customer_id))).where(
        StampLog.scanned_at >= window_start,
        StampLog.is_fraudulent.is_(False),
        StampLog.customer_id.is_not(None),
    )
    if branch_id is not None:
        query = query.where(StampLog.branch_id == branch_id)
    result = await session.execute(query)
    return result.scalar_one()


async def _heatmap(
    session: AsyncSession, window_start: datetime, branch_id: uuid.UUID | None
) -> list[HeatmapPoint]:
    hour = func.extract("hour", StampLog.scanned_at)
    query = (
        select(StampLog.branch_id, hour.label("hour"), func.count(StampLog.id))
        .where(StampLog.scanned_at >= window_start, StampLog.is_fraudulent.is_(False))
        .group_by(StampLog.branch_id, hour)
    )
    if branch_id is not None:
        query = query.where(StampLog.branch_id == branch_id)

    result = await session.execute(query)
    return [
        HeatmapPoint(branch_id=row[0], hour=int(row[1]), scan_count=row[2]) for row in result.all()
    ]


async def _top_customers(session: AsyncSession) -> list[TopCustomer]:
    result = await session.execute(
        select(Customer)
        .where(Customer.total_stamps_alltime > 0)
        .order_by(Customer.total_stamps_alltime.desc())
        .limit(TOP_CUSTOMERS_LIMIT)
    )
    return [
        TopCustomer(
            customer_id=customer.id,
            phone_masked=_mask_phone(decrypt_pii(customer.encrypted_phone)),
            total_stamps=customer.total_stamps_alltime,
        )
        for customer in result.scalars().all()
    ]


async def _branch_comparison(
    session: AsyncSession, window_start: datetime, branch_id: uuid.UUID | None
) -> list[BranchComparisonPoint]:
    query = (
        select(StampLog.branch_id, Branch.name, func.count(StampLog.id))
        .join(Branch, Branch.id == StampLog.branch_id)
        .where(StampLog.scanned_at >= window_start, StampLog.is_fraudulent.is_(False))
        .group_by(StampLog.branch_id, Branch.name)
    )
    if branch_id is not None:
        query = query.where(StampLog.branch_id == branch_id)

    result = await session.execute(query)
    return [
        BranchComparisonPoint(branch_id=row[0], branch_name=row[1], scan_count=row[2])
        for row in result.all()
    ]


async def _redemption_rate(session: AsyncSession) -> float:
    total_result = await session.execute(select(func.count(RewardRedemption.id)))
    total = total_result.scalar_one()
    if total == 0:
        return 0.0

    redeemed_result = await session.execute(
        select(func.count(RewardRedemption.id)).where(RewardRedemption.redeemed_at.is_not(None))
    )
    redeemed = redeemed_result.scalar_one()
    return round(redeemed / total, 3)


async def _fraud_log(session: AsyncSession, branch_id: uuid.UUID | None) -> list[FraudLogEntry]:
    query = (
        select(StampLog)
        .where(StampLog.is_fraudulent.is_(True))
        .order_by(StampLog.scanned_at.desc())
        .limit(FRAUD_LOG_LIMIT)
    )
    if branch_id is not None:
        query = query.where(StampLog.branch_id == branch_id)

    result = await session.execute(query)
    return [
        FraudLogEntry(
            scanned_at=entry.scanned_at,
            branch_id=entry.branch_id,
            distance_from_branch_m=entry.distance_from_branch_m,
            reason=_ONLY_FRAUD_REASON,
        )
        for entry in result.scalars().all()
    ]
