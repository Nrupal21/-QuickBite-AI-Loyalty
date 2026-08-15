"""Unit tests for dashboard_service.get_loyalty_analytics (DASH-02).

DB session is mocked per house convention. Query call order inside
get_loyalty_analytics is fixed: total_stamps_month, active_loyalty_customers,
heatmap, top_customers, branch_comparison, redemption_rate (1-2 calls),
fraud_log — make_session's execute_results list follows that order, same
discipline test_dashboard_service.py uses for get_stats.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.encryption import encrypt_pii
from app.db.models.customer import Customer
from app.services import dashboard_service

TENANT_ID = uuid.uuid4()
BRANCH_ID = uuid.uuid4()


def make_result(*, all_rows=None, scalars_all=None, scalar=None) -> MagicMock:
    result = MagicMock()
    result.all.return_value = all_rows or []
    result.scalars.return_value.all.return_value = scalars_all or []
    result.scalar_one.return_value = scalar
    return result


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_results)
    return session


def make_customer(phone: str, total_stamps: int) -> Customer:
    customer = Customer(
        tenant_id=TENANT_ID,
        phone_hash="irrelevant",
        encrypted_phone=encrypt_pii(phone),
        total_stamps_alltime=total_stamps,
    )
    customer.id = uuid.uuid4()
    return customer


# Leading two results every call issues before the ones a test cares about:
# total_stamps_month, active_loyalty_customers.
_HEADER = [make_result(scalar=0), make_result(scalar=0)]


@pytest.mark.asyncio
async def test_total_stamps_month_and_active_customers_reported():
    session = make_session(
        [
            make_result(scalar=57),  # total_stamps_month
            make_result(scalar=13),  # active_loyalty_customers
            make_result(all_rows=[]),  # heatmap
            make_result(scalars_all=[]),  # top_customers
            make_result(all_rows=[]),  # branch_comparison
            make_result(scalar=0),  # redemption_rate: total=0, short-circuits
            make_result(scalars_all=[]),  # fraud_log
        ]
    )

    response = await dashboard_service.get_loyalty_analytics(session, branch_id=None)

    assert response.total_stamps_month == 57
    assert response.active_loyalty_customers == 13


@pytest.mark.asyncio
async def test_heatmap_reports_counts_per_hour_per_branch():
    heatmap_rows = [(BRANCH_ID, 18, 5), (BRANCH_ID, 19, 3)]
    session = make_session(
        [
            *_HEADER,
            make_result(all_rows=heatmap_rows),  # heatmap
            make_result(scalars_all=[]),  # top_customers
            make_result(all_rows=[]),  # branch_comparison
            make_result(scalar=0),  # redemption_rate: total=0, short-circuits
            make_result(scalars_all=[]),  # fraud_log
        ]
    )

    response = await dashboard_service.get_loyalty_analytics(session, branch_id=None)

    assert len(response.heatmap) == 2
    assert response.heatmap[0].branch_id == BRANCH_ID
    assert response.heatmap[0].hour == 18
    assert response.heatmap[0].scan_count == 5


@pytest.mark.asyncio
async def test_top_customers_masks_phone_to_last_four_digits():
    customer = make_customer("+919876543210", total_stamps=42)
    session = make_session(
        [
            *_HEADER,
            make_result(all_rows=[]),
            make_result(scalars_all=[customer]),  # top_customers
            make_result(all_rows=[]),
            make_result(scalar=0),
            make_result(scalars_all=[]),
        ]
    )

    response = await dashboard_service.get_loyalty_analytics(session, branch_id=None)

    assert len(response.top_customers) == 1
    top = response.top_customers[0]
    assert top.phone_masked == "*** **** 3210"
    assert "9876543210" not in top.phone_masked
    assert top.total_stamps == 42


@pytest.mark.asyncio
async def test_branch_comparison_reports_scan_counts():
    session = make_session(
        [
            *_HEADER,
            make_result(all_rows=[]),
            make_result(scalars_all=[]),
            make_result(all_rows=[(BRANCH_ID, "Marco's Koramangala", 20)]),  # branch_comparison
            make_result(scalar=0),
            make_result(scalars_all=[]),
        ]
    )

    response = await dashboard_service.get_loyalty_analytics(session, branch_id=None)

    assert len(response.branch_comparison) == 1
    assert response.branch_comparison[0].branch_name == "Marco's Koramangala"
    assert response.branch_comparison[0].scan_count == 20


@pytest.mark.asyncio
async def test_redemption_rate_is_redeemed_over_generated():
    session = make_session(
        [
            *_HEADER,
            make_result(all_rows=[]),
            make_result(scalars_all=[]),
            make_result(all_rows=[]),
            make_result(scalar=10),  # total generated
            make_result(scalar=4),  # redeemed
            make_result(scalars_all=[]),
        ]
    )

    response = await dashboard_service.get_loyalty_analytics(session, branch_id=None)

    assert response.redemption_rate == 0.4


@pytest.mark.asyncio
async def test_redemption_rate_zero_when_none_generated():
    session = make_session(
        [
            *_HEADER,
            make_result(all_rows=[]),
            make_result(scalars_all=[]),
            make_result(all_rows=[]),
            make_result(scalar=0),  # total=0, no second query
            make_result(scalars_all=[]),
        ]
    )

    response = await dashboard_service.get_loyalty_analytics(session, branch_id=None)

    assert response.redemption_rate == 0.0


@pytest.mark.asyncio
async def test_fraud_log_reports_scan_and_distance_and_reason():
    from app.db.models.loyalty import StampLog

    entry = StampLog(
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        gps_latitude_at_scan=12.9,
        gps_longitude_at_scan=77.5,
        distance_from_branch_m=350.0,
        is_fraudulent=True,
        scanned_at=datetime.now(UTC),
    )
    session = make_session(
        [
            *_HEADER,
            make_result(all_rows=[]),
            make_result(scalars_all=[]),
            make_result(all_rows=[]),
            make_result(scalar=0),
            make_result(scalars_all=[entry]),  # fraud_log
        ]
    )

    response = await dashboard_service.get_loyalty_analytics(session, branch_id=None)

    assert len(response.fraud_log) == 1
    assert response.fraud_log[0].distance_from_branch_m == 350.0
    assert response.fraud_log[0].reason == "Outside geofence radius"


@pytest.mark.asyncio
async def test_branch_id_filter_is_applied_to_heatmap_query():
    session = make_session(
        [
            *_HEADER,
            make_result(all_rows=[]),
            make_result(scalars_all=[]),
            make_result(all_rows=[]),
            make_result(scalar=0),
            make_result(scalars_all=[]),
        ]
    )

    await dashboard_service.get_loyalty_analytics(session, branch_id=BRANCH_ID)

    heatmap_query = session.execute.await_args_list[2].args[0]
    assert "branch_id" in str(heatmap_query.whereclause)


@pytest.mark.asyncio
async def test_branch_id_filter_is_applied_to_total_stamps_and_active_customers():
    session = make_session(
        [
            make_result(scalar=0),
            make_result(scalar=0),
            make_result(all_rows=[]),
            make_result(scalars_all=[]),
            make_result(all_rows=[]),
            make_result(scalar=0),
            make_result(scalars_all=[]),
        ]
    )

    await dashboard_service.get_loyalty_analytics(session, branch_id=BRANCH_ID)

    total_stamps_query = session.execute.await_args_list[0].args[0]
    active_customers_query = session.execute.await_args_list[1].args[0]
    assert "branch_id" in str(total_stamps_query.whereclause)
    assert "branch_id" in str(active_customers_query.whereclause)
