"""Unit tests for LoyaltyService.process_scan — one per LOYALTY-03 acceptance criterion.

DB session and external services (PostGIS distance query, Redis) are mocked,
per AGENTS.md testing rules — these are service-layer unit tests, not the
full integration flow against real Postgres/Redis.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.db.models.branch import Branch
from app.db.models.customer import Customer
from app.services.loyalty_service import LoyaltyService

BRANCH_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()


def make_branch(radius_m: int = 100) -> Branch:
    branch = Branch(
        tenant_id=TENANT_ID,
        name="Test Branch",
        address_hash="a" * 64,
        encrypted_address="encrypted-address-blob",
        location="POINT(77.5946 12.9716)",
        geofence_radius_m=radius_m,
        qr_code_token="valid-token-1234567890",
        is_active=True,
    )
    branch.id = BRANCH_ID
    return branch


def make_session(branch: Branch | None) -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = branch
    session.execute.return_value = execute_result
    return session


@pytest.mark.asyncio
async def test_scan_inside_geofence_first_scan_returns_stamp(mocker):
    branch = make_branch()
    session = make_session(branch)
    mocker.patch("app.services.loyalty_service.distance_to_branch_m", AsyncMock(return_value=10.0))
    mocker.patch("app.core.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.core.cache_service.set", AsyncMock())

    service = LoyaltyService(session=session)
    response = await service.process_scan(
        qr_token="valid-token-1234567890",
        gps_lat=12.9716,
        gps_lng=77.5946,
        client_ip="1.2.3.4",
    )

    assert response.stamp_count == 1
    assert response.reward_unlocked is False
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_scan_outside_geofence_returns_403_and_logs_fraudulent(mocker):
    branch = make_branch(radius_m=100)
    session = make_session(branch)
    mocker.patch("app.services.loyalty_service.distance_to_branch_m", AsyncMock(return_value=5000.0))

    service = LoyaltyService(session=session)
    with pytest.raises(HTTPException) as exc_info:
        await service.process_scan(
            qr_token="valid-token-1234567890",
            gps_lat=0.0,
            gps_lng=0.0,
            client_ip="1.2.3.4",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "GEOFENCE_OUT_OF_RANGE"
    added_log = session.add.call_args[0][0]
    assert added_log.is_fraudulent is True


@pytest.mark.asyncio
async def test_scan_within_rate_limit_window_returns_429(mocker):
    branch = make_branch()
    session = make_session(branch)
    mocker.patch("app.services.loyalty_service.distance_to_branch_m", AsyncMock(return_value=10.0))
    mocker.patch("app.core.cache_service.exists", AsyncMock(return_value=True))

    service = LoyaltyService(session=session)
    with pytest.raises(HTTPException) as exc_info:
        await service.process_scan(
            qr_token="valid-token-1234567890",
            gps_lat=12.9716,
            gps_lng=77.5946,
            client_ip="1.2.3.4",
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "GEOFENCE_RATE_LIMIT"
    assert exc_info.value.headers["Retry-After"] == "3600"


@pytest.mark.asyncio
async def test_scan_invalid_qr_token_returns_400():
    session = make_session(branch=None)

    service = LoyaltyService(session=session)
    with pytest.raises(HTTPException) as exc_info:
        await service.process_scan(
            qr_token="does-not-exist-00000",
            gps_lat=12.9716,
            gps_lng=77.5946,
            client_ip="1.2.3.4",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "LOYALTY_INVALID_QR"


@pytest.mark.asyncio
async def test_scan_links_customer_id_when_session_active(mocker):
    branch = make_branch()
    session = make_session(branch)
    mocker.patch("app.services.loyalty_service.distance_to_branch_m", AsyncMock(return_value=10.0))
    mocker.patch("app.core.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.core.cache_service.set", AsyncMock())

    customer = Customer(
        tenant_id=TENANT_ID,
        phone_hash="phonehash123",
        encrypted_phone="encrypted",
        total_stamps_alltime=2,
        current_reward_count=2,
    )
    customer.id = uuid.uuid4()

    service = LoyaltyService(session=session)
    response = await service.process_scan(
        qr_token="valid-token-1234567890",
        gps_lat=12.9716,
        gps_lng=77.5946,
        client_ip="1.2.3.4",
        customer=customer,
    )

    added_log = session.add.call_args[0][0]
    assert added_log.customer_id == customer.id
    assert customer.total_stamps_alltime == 3
    assert customer.current_reward_count == 3
    assert response.stamp_count == 3
