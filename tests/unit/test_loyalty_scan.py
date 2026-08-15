"""Unit tests for LoyaltyService — scan processing (LOYALTY-03) and reward
programs + redemption codes (LOYALTY-04), one test per acceptance criterion.

DB session and external services (PostGIS distance query, Redis, Twilio) are
mocked, per AGENTS.md testing rules — these are service-layer unit tests, not
the full integration flow against real Postgres/Redis.

`make_session`'s `session.execute` returns the same mocked Branch-shaped
result for every call by default (bootstrap's tenant resolver, rls's
set_tenant_context, and the branch SELECT all go through session.execute, and
none of the LOYALTY-03 tests care what those intermediate calls return) —
except a query on `reward_programs`, which is routed to a separate mocked
result via `reward_program=`, since LOYALTY-04 added that as a second,
differently-shaped query in the same call.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.db.models.audit import AuditLog
from app.db.models.branch import Branch
from app.db.models.customer import Customer
from app.db.models.loyalty import RewardProgram, RewardRedemption
from app.db.models.user import User
from app.schemas.loyalty import RewardProgramCreateRequest
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


def make_reward_program(**overrides) -> RewardProgram:
    defaults = {
        "tenant_id": TENANT_ID,
        "branch_id": BRANCH_ID,
        "name": "10 stamps = free coffee",
        "stamps_required": 5,
        "reward_type": "free_item",
        "reward_value": "Coffee",
        "validity_days": 7,
    }
    defaults.update(overrides)
    program = RewardProgram(**defaults)
    program.id = uuid.uuid4()
    return program


def make_customer(**overrides) -> Customer:
    defaults = {
        "tenant_id": TENANT_ID,
        "phone_hash": "phonehash123",
        "encrypted_phone": "encrypted",
        "total_stamps_alltime": 0,
        "current_reward_count": 0,
        "whatsapp_opt_in": False,
    }
    defaults.update(overrides)
    customer = Customer(**defaults)
    customer.id = uuid.uuid4()
    return customer


def make_session(branch: Branch | None, reward_program: RewardProgram | None = None) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()

    branch_result = MagicMock()
    branch_result.scalar_one_or_none.return_value = branch
    program_result = MagicMock()
    program_result.scalar_one_or_none.return_value = reward_program

    async def _execute(statement, *args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        if "reward_programs" in str(statement):
            return program_result
        return branch_result

    session.execute = AsyncMock(side_effect=_execute)
    return session


def added(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


@pytest.fixture(autouse=True)
def _mock_broadcast(mocker):
    """DASH-01's dashboard-refresh nudge — mocked so these tests never touch
    a real Redis connection, matching this file's cache_service mocks."""
    mocker.patch("app.services.loyalty_service.broadcast.publish_event", AsyncMock())


# --- LOYALTY-03: scan --------------------------------------------------


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
    session = make_session(branch)  # no active reward program
    mocker.patch("app.services.loyalty_service.distance_to_branch_m", AsyncMock(return_value=10.0))
    mocker.patch("app.core.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.core.cache_service.set", AsyncMock())

    customer = make_customer(total_stamps_alltime=2, current_reward_count=2)

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


# --- LOYALTY-04: reward unlock on scan ----------------------------------


@pytest.mark.asyncio
async def test_scan_below_threshold_returns_progress_no_unlock(mocker):
    branch = make_branch()
    program = make_reward_program(stamps_required=5)
    session = make_session(branch, reward_program=program)
    mocker.patch("app.services.loyalty_service.distance_to_branch_m", AsyncMock(return_value=10.0))
    mocker.patch("app.core.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.core.cache_service.set", AsyncMock())

    customer = make_customer(current_reward_count=2)  # becomes 3 after this scan

    response = await LoyaltyService(session=session).process_scan(
        qr_token="valid-token-1234567890",
        gps_lat=12.9716,
        gps_lng=77.5946,
        client_ip="1.2.3.4",
        customer=customer,
    )

    assert response.reward_unlocked is False
    assert response.stamp_count == 3
    assert response.reward_progress == 3 / 5
    assert response.next_reward_at == 2
    assert response.redemption_code is None
    assert added(session, RewardRedemption) == []


@pytest.mark.asyncio
async def test_scan_reaching_threshold_generates_redemption_code(mocker):
    branch = make_branch()
    program = make_reward_program(stamps_required=5, validity_days=7)
    session = make_session(branch, reward_program=program)
    mocker.patch("app.services.loyalty_service.distance_to_branch_m", AsyncMock(return_value=10.0))
    mocker.patch("app.core.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.core.cache_service.set", AsyncMock())

    customer = make_customer(current_reward_count=4)  # becomes 5 == stamps_required

    response = await LoyaltyService(session=session).process_scan(
        qr_token="valid-token-1234567890",
        gps_lat=12.9716,
        gps_lng=77.5946,
        client_ip="1.2.3.4",
        customer=customer,
    )

    assert response.reward_unlocked is True
    assert response.stamp_count == 5
    assert response.reward_progress == 1.0
    assert response.validity_days == 7
    assert len(response.redemption_code) == 6
    assert response.redemption_code.isalnum()
    # Cycle resets for the next reward.
    assert customer.current_reward_count == 0

    redemption = added(session, RewardRedemption)[0]
    assert redemption.code == response.redemption_code
    assert redemption.customer_id == customer.id
    assert redemption.reward_program_id == program.id


@pytest.mark.asyncio
async def test_scan_unlock_sends_whatsapp_when_opted_in_and_plan_allows(mocker):
    branch = make_branch()
    program = make_reward_program(stamps_required=1, validity_days=7)
    session = make_session(branch, reward_program=program)
    mocker.patch("app.services.loyalty_service.distance_to_branch_m", AsyncMock(return_value=10.0))
    mocker.patch("app.core.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.core.cache_service.set", AsyncMock())
    mocker.patch("app.services.loyalty_service.tenant_has_feature", AsyncMock(return_value=True))
    mocker.patch("app.services.loyalty_service.decrypt_pii", MagicMock(return_value="+919876543210"))
    send_whatsapp = mocker.patch(
        "app.services.loyalty_service.messaging_service.send_reward_unlocked_whatsapp",
        AsyncMock(return_value=True),
    )

    customer = make_customer(current_reward_count=0, whatsapp_opt_in=True)

    await LoyaltyService(session=session).process_scan(
        qr_token="valid-token-1234567890",
        gps_lat=12.9716,
        gps_lng=77.5946,
        client_ip="1.2.3.4",
        customer=customer,
    )

    send_whatsapp.assert_awaited_once()
    args = send_whatsapp.await_args.args
    assert args[0] == "+919876543210"
    assert args[1] == branch.name


@pytest.mark.asyncio
async def test_scan_unlock_skips_whatsapp_when_plan_lacks_feature(mocker):
    branch = make_branch()
    program = make_reward_program(stamps_required=1)
    session = make_session(branch, reward_program=program)
    mocker.patch("app.services.loyalty_service.distance_to_branch_m", AsyncMock(return_value=10.0))
    mocker.patch("app.core.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.core.cache_service.set", AsyncMock())
    mocker.patch("app.services.loyalty_service.tenant_has_feature", AsyncMock(return_value=False))
    send_whatsapp = mocker.patch(
        "app.services.loyalty_service.messaging_service.send_reward_unlocked_whatsapp",
        AsyncMock(),
    )

    customer = make_customer(current_reward_count=0, whatsapp_opt_in=True)

    await LoyaltyService(session=session).process_scan(
        qr_token="valid-token-1234567890",
        gps_lat=12.9716,
        gps_lng=77.5946,
        client_ip="1.2.3.4",
        customer=customer,
    )

    send_whatsapp.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_unlock_skips_whatsapp_when_not_opted_in(mocker):
    branch = make_branch()
    program = make_reward_program(stamps_required=1)
    session = make_session(branch, reward_program=program)
    mocker.patch("app.services.loyalty_service.distance_to_branch_m", AsyncMock(return_value=10.0))
    mocker.patch("app.core.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.core.cache_service.set", AsyncMock())
    send_whatsapp = mocker.patch(
        "app.services.loyalty_service.messaging_service.send_reward_unlocked_whatsapp",
        AsyncMock(),
    )

    customer = make_customer(current_reward_count=0, whatsapp_opt_in=False)

    await LoyaltyService(session=session).process_scan(
        qr_token="valid-token-1234567890",
        gps_lat=12.9716,
        gps_lng=77.5946,
        client_ip="1.2.3.4",
        customer=customer,
    )

    send_whatsapp.assert_not_awaited()


# --- LOYALTY-04: POST /reward-programs ----------------------------------


@pytest.mark.asyncio
async def test_create_reward_program_with_valid_branch():
    owner = User(tenant_id=TENANT_ID, role_id=uuid.uuid4())
    owner.id = uuid.uuid4()
    session = make_session(branch=make_branch())  # branch_id lookup finds it

    response = await LoyaltyService(session=session).create_reward_program(
        RewardProgramCreateRequest(
            branch_id=BRANCH_ID,
            name="Free Coffee",
            stamps_required=5,
            reward_type="free_item",
            reward_value="Coffee",
            validity_days=7,
        ),
        owner,
    )

    assert response.name == "Free Coffee"
    assert response.stamps_required == 5
    created = added(session, RewardProgram)[0]
    assert created.tenant_id == owner.tenant_id
    assert created.branch_id == BRANCH_ID


@pytest.mark.asyncio
async def test_create_reward_program_unknown_branch_returns_404():
    owner = User(tenant_id=TENANT_ID, role_id=uuid.uuid4())
    owner.id = uuid.uuid4()
    session = make_session(branch=None)  # RLS hides another tenant's branch too

    with pytest.raises(HTTPException) as exc_info:
        await LoyaltyService(session=session).create_reward_program(
            RewardProgramCreateRequest(
                branch_id=uuid.uuid4(),
                name="Free Coffee",
                stamps_required=5,
                reward_type="free_item",
                reward_value="Coffee",
                validity_days=7,
            ),
            owner,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "BRANCH_NOT_FOUND"


def test_reward_program_schema_rejects_invalid_stamps_required():
    with pytest.raises(ValidationError):
        RewardProgramCreateRequest(
            branch_id=uuid.uuid4(),
            name="Bad",
            stamps_required=0,
            reward_type="free_item",
            reward_value="Coffee",
            validity_days=7,
        )


# --- LOYALTY-04: POST /loyalty/redeem/{code} ----------------------------


def make_redemption(**overrides) -> RewardRedemption:
    defaults = {
        "tenant_id": TENANT_ID,
        "branch_id": BRANCH_ID,
        "reward_program_id": uuid.uuid4(),
        "customer_id": uuid.uuid4(),
        "code": "ABC123",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
        "redeemed_at": None,
        "redeemed_by_user_id": None,
    }
    defaults.update(overrides)
    redemption = RewardRedemption(**defaults)
    redemption.id = uuid.uuid4()
    return redemption


def make_redeem_session(redemption: RewardRedemption | None) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = redemption
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_redeem_valid_code_marks_redeemed_and_audit_logs():
    staff = User(tenant_id=TENANT_ID, role_id=uuid.uuid4())
    staff.id = uuid.uuid4()
    redemption = make_redemption()
    session = make_redeem_session(redemption)

    response = await LoyaltyService(session=session).redeem_code("ABC123", staff)

    assert response.status == "redeemed"
    assert redemption.redeemed_at is not None
    assert redemption.redeemed_by_user_id == staff.id
    entry = added(session, AuditLog)[-1]
    assert entry.action == "reward_redeemed"


@pytest.mark.asyncio
async def test_redeem_unknown_code_returns_404():
    staff = User(tenant_id=TENANT_ID, role_id=uuid.uuid4())
    staff.id = uuid.uuid4()
    session = make_redeem_session(None)

    with pytest.raises(HTTPException) as exc_info:
        await LoyaltyService(session=session).redeem_code("NOPE00", staff)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "REDEMPTION_CODE_NOT_FOUND"


@pytest.mark.asyncio
async def test_redeem_already_used_code_returns_409():
    staff = User(tenant_id=TENANT_ID, role_id=uuid.uuid4())
    staff.id = uuid.uuid4()
    redemption = make_redemption(redeemed_at=datetime.now(timezone.utc))
    session = make_redeem_session(redemption)

    with pytest.raises(HTTPException) as exc_info:
        await LoyaltyService(session=session).redeem_code("ABC123", staff)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "REDEMPTION_CODE_ALREADY_USED"


@pytest.mark.asyncio
async def test_redeem_expired_code_returns_410():
    staff = User(tenant_id=TENANT_ID, role_id=uuid.uuid4())
    staff.id = uuid.uuid4()
    redemption = make_redemption(expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    session = make_redeem_session(redemption)

    with pytest.raises(HTTPException) as exc_info:
        await LoyaltyService(session=session).redeem_code("ABC123", staff)

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail["error"]["code"] == "REDEMPTION_CODE_EXPIRED"
