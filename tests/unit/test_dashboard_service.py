"""Unit tests for dashboard_service.get_stats (DASH-01).

DB session is mocked per house convention (see test_team.py's make_session).
Each `session.execute()` call is scripted in the exact order get_stats issues
them: avg_rating, review_count, pending_approvals, scans_today, fraud_today,
sentiment_trend — the same ordering discipline test_rbac.py/test_team.py use
for their own session.execute sequences.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import dashboard_service


def make_result(*, scalar=None, rows=None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.scalar_one.return_value = scalar
    result.all.return_value = rows or []
    return result


def make_session(*, avg_rating, review_count, pending, scans_today, fraud_today, trend_rows):
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            make_result(scalar=avg_rating),
            make_result(scalar=review_count),
            make_result(scalar=pending),
            make_result(scalar=scans_today),
            make_result(scalar=fraud_today),
            make_result(rows=trend_rows),
        ]
    )
    return session


@pytest.mark.asyncio
async def test_get_stats_returns_aggregated_counts():
    session = make_session(
        avg_rating=4.5,
        review_count=12,
        pending=3,
        scans_today=7,
        fraud_today=1,
        trend_rows=[],
    )

    response = await dashboard_service.get_stats(session, can_approve=True)

    assert response.avg_rating == 4.5
    assert response.review_count == 12
    assert response.pending_approvals == 3
    assert response.loyalty_scans_today == 7
    assert response.fraud_alert_count == 1
    assert response.can_approve is True


@pytest.mark.asyncio
async def test_get_stats_avg_rating_none_when_no_reviews():
    session = make_session(
        avg_rating=None, review_count=0, pending=0, scans_today=0, fraud_today=0, trend_rows=[]
    )

    response = await dashboard_service.get_stats(session, can_approve=False)

    assert response.avg_rating is None
    assert response.review_count == 0


@pytest.mark.asyncio
async def test_get_stats_can_approve_reflects_caller_role():
    session = make_session(
        avg_rating=None, review_count=0, pending=0, scans_today=0, fraud_today=0, trend_rows=[]
    )

    response = await dashboard_service.get_stats(session, can_approve=False)

    assert response.can_approve is False


@pytest.mark.asyncio
async def test_sentiment_trend_fills_missing_days_with_none():
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    two_days_ago = today - timedelta(days=2)
    session = make_session(
        avg_rating=None,
        review_count=0,
        pending=0,
        scans_today=0,
        fraud_today=0,
        trend_rows=[(today, 0.8), (two_days_ago, -0.2)],
    )

    response = await dashboard_service.get_stats(session, can_approve=True)

    assert len(response.sentiment_trend) == dashboard_service.SENTIMENT_TREND_DAYS
    by_date = {p.date: p.avg_sentiment for p in response.sentiment_trend}
    assert by_date[today.date().isoformat()] == 0.8
    assert by_date[two_days_ago.date().isoformat()] == -0.2
    # A day with no reviews reads as None, not 0 — "no data" and "neutral
    # sentiment" must stay distinguishable.
    yesterday = (today - timedelta(days=1)).date().isoformat()
    assert by_date[yesterday] is None
