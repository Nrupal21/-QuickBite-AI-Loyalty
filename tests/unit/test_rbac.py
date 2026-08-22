"""Unit tests for the AUTH-04 dependency guards — one per acceptance criterion.

Covers get_current_user(), require_role(), and check_subscription_tier().
DB session and Redis are mocked per AGENTS.md testing rules.

Role.level is a rank, not a count: 1=Super Admin .. 4=Staff, so `require_role`
admits anyone whose level is <= the argument. These tests pin that direction
down, because inverting the comparison would silently grant Staff everything.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import HTTPException

from app.api.v1.dependencies.auth import get_current_user, require_role
from app.api.v1.dependencies.subscription import check_subscription_tier
from app.core.config import settings
from app.core.encryption import encrypt_pii, sha256_hex
from app.core.rbac import RoleLevel
from app.core.security import create_access_token, hash_password
from app.db.models.subscription import SubscriptionPlan
from app.db.models.tenant import Tenant
from app.db.models.user import User

TENANT_ID = uuid.uuid4()
ROLE_ID = uuid.uuid4()
EMAIL = "manager@marcos.in"


def make_session(execute_results: list) -> MagicMock:
    """`execute_results` are consumed in call order; scalar_one_or_none() only."""
    session = MagicMock()
    session.commit = AsyncMock()
    results = []
    for value in execute_results:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)
    session.execute = AsyncMock(side_effect=results)
    return session


def make_user(**overrides) -> User:
    defaults = {
        "tenant_id": TENANT_ID,
        "role_id": ROLE_ID,
        "email_hash": sha256_hex(EMAIL),
        "encrypted_email": encrypt_pii(EMAIL),
        "hashed_password": hash_password("irrelevant-here"),
        "mfa_enabled": False,
        "email_verified": True,
        "is_active": True,
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.id = uuid.uuid4()
    return user


def make_request(token: str | None) -> MagicMock:
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {token}"} if token else {}
    request.state = MagicMock()
    return request


# --- get_current_user ---------------------------------------------------


@pytest.mark.asyncio
async def test_expired_jwt_returns_401(mocker):
    """AUTH-04: expired JWT on any protected endpoint → 401."""
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False)
    )
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": str(TENANT_ID),
            "role": "OWNER",
            "jti": str(uuid.uuid4()),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    session = make_session([])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(expired), session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_jwt_forged_with_alg_none_returns_401(mocker):
    """SEC-03: an unsigned token must never authenticate (decode pins HS256)."""
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False)
    )
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": str(TENANT_ID),
            "role": "SUPER_ADMIN",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        key="",
        algorithm="none",
    )
    session = make_session([])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(forged), session)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_revoked_jti_returns_401(mocker):
    """AUTH-03: a logged-out token's jti sits in the Redis blocklist."""
    mocker.patch("app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=True))
    user = make_user()
    token = create_access_token(user.id, TENANT_ID, "OWNER")
    session = make_session([user])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(token), session)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_sets_app_tenant_id_on_the_db_session(mocker):
    """AUTH-04: app.tenant_id set on every authenticated session, or RLS returns
    zero rows on every tenant-scoped table."""
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False)
    )
    user = make_user()
    token = create_access_token(user.id, TENANT_ID, "OWNER")
    session = make_session([None, user, True])

    returned = await get_current_user(make_request(token), session)

    assert returned is user
    # First, not second: under RLS the user row is invisible until the tenant
    # is bound, so binding after the read would return zero rows and 401 every
    # authenticated request. Bound from the signed tenant_id claim.
    set_config_call = session.execute.await_args_list[0]
    assert "set_config" in str(set_config_call.args[0])
    assert set_config_call.args[1] == {"tenant_id": str(TENANT_ID)}
    # `true` = transaction-local, so tenant context never leaks across requests.
    assert "true" in str(set_config_call.args[0])


@pytest.mark.asyncio
async def test_suspended_tenant_returns_403_even_with_valid_token(mocker):
    """A suspended tenant's already-issued, still-unexpired JWT must stop
    working immediately — suspension is enforced here, not only hidden in
    the admin UI."""
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False)
    )
    user = make_user()
    token = create_access_token(user.id, TENANT_ID, "OWNER")
    # set_config, select(User), select(Tenant.is_active) — in that order.
    session = make_session([None, user, False])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(token), session)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "TENANT_SUSPENDED"


@pytest.mark.asyncio
async def test_deactivated_user_returns_403_before_token_expiry(mocker):
    """A removed member's access token stays signed until it lapses — the row
    check is what revokes them immediately."""
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False)
    )
    user = make_user(is_active=False)
    token = create_access_token(user.id, TENANT_ID, "STAFF")
    # set_config consumes the first result; the user row is the second.
    session = make_session([None, user])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(token), session)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "ACCOUNT_DEACTIVATED"


@pytest.mark.asyncio
async def test_missing_authorization_header_returns_401():
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(None), make_session([]))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_increments_api_call_counter(mocker):
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False)
    )
    incr = mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.incr", AsyncMock(return_value=1)
    )
    user = make_user()
    token = create_access_token(user.id, TENANT_ID, "OWNER")
    session = make_session([None, user, True])

    await get_current_user(make_request(token), session)

    incr.assert_awaited_once()
    key = incr.await_args.args[0]
    assert key.startswith(f"api_calls:{TENANT_ID}:")


@pytest.mark.asyncio
async def test_get_current_user_survives_redis_outage_during_counting(mocker):
    """A Redis hiccup on the analytics counter must never fail the actual
    request — it's a nice-to-have, not a correctness dependency."""
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False)
    )
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.incr",
        AsyncMock(side_effect=ConnectionError("redis down")),
    )
    user = make_user()
    token = create_access_token(user.id, TENANT_ID, "OWNER")
    session = make_session([None, user, True])

    returned = await get_current_user(make_request(token), session)

    assert returned is user


# --- require_role -------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_on_manager_only_endpoint_returns_403():
    """AUTH-04: Staff (level 4) on a Manager-only route (level 3) → 403."""
    user = make_user()
    session = make_session([4])  # scalar Role.level for STAFF
    guard = require_role(RoleLevel.MANAGER)

    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=user, session=session)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "INSUFFICIENT_PERMISSIONS"


@pytest.mark.asyncio
async def test_owner_on_super_admin_endpoint_returns_403():
    """AUTH-04: Owner (level 2) on a Super Admin route (level 1) → 403."""
    user = make_user()
    session = make_session([2])
    guard = require_role(RoleLevel.SUPER_ADMIN)

    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=user, session=session)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_manager_on_manager_endpoint_is_allowed():
    user = make_user()
    session = make_session([3])
    guard = require_role(RoleLevel.MANAGER)

    assert await guard(current_user=user, session=session) is user


@pytest.mark.asyncio
async def test_owner_on_manager_endpoint_is_allowed():
    """Seniority is inclusive downward: an Owner may do anything a Manager may."""
    user = make_user()
    session = make_session([2])
    guard = require_role(RoleLevel.MANAGER)

    assert await guard(current_user=user, session=session) is user


@pytest.mark.asyncio
async def test_super_admin_passes_every_guard():
    for min_level in (RoleLevel.SUPER_ADMIN, RoleLevel.OWNER, RoleLevel.MANAGER, RoleLevel.STAFF):
        user = make_user()
        session = make_session([1])
        assert await require_role(min_level)(current_user=user, session=session) is user


@pytest.mark.asyncio
async def test_missing_role_row_returns_403_not_a_crash():
    """A user pointing at a deleted role must fail closed, never fail open."""
    user = make_user()
    session = make_session([None])
    guard = require_role(RoleLevel.STAFF)

    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=user, session=session)

    assert exc_info.value.status_code == 403


# --- check_subscription_tier --------------------------------------------


def make_plan(name: str, feature_limits: dict) -> SubscriptionPlan:
    plan = SubscriptionPlan(name=name, feature_limits=feature_limits)
    plan.id = uuid.uuid4()
    return plan


def make_tenant(plan_id: uuid.UUID | None) -> Tenant:
    tenant = Tenant(subdomain="marcos", name="Marco's", plan_id=plan_id)
    tenant.id = TENANT_ID
    return tenant


@pytest.mark.asyncio
async def test_starter_plan_blocked_from_pro_feature_returns_402():
    """AUTH-04: Starter on a Pro feature → 402 carrying upgrade data."""
    plan = make_plan("Starter", {"multi_branch": False})
    user = make_user()
    session = make_session([make_tenant(plan.id), plan])
    guard = check_subscription_tier("multi_branch")

    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=user, session=session)

    assert exc_info.value.status_code == 402
    detail = exc_info.value.detail["error"]
    assert detail["code"] == "PLAN_UPGRADE_REQUIRED"
    assert detail["feature"] == "multi_branch"
    assert detail["current_plan"] == "Starter"
    assert detail["upgrade_url"]


@pytest.mark.asyncio
async def test_pro_plan_granted_feature_is_allowed():
    plan = make_plan("Pro", {"multi_branch": True, "whatsapp": True})
    user = make_user()
    session = make_session([make_tenant(plan.id), plan])
    guard = check_subscription_tier("multi_branch")

    assert await guard(current_user=user, session=session) is user


@pytest.mark.asyncio
async def test_feature_absent_from_plan_limits_returns_402():
    """An unknown key is a denial, not a grant — new features stay gated until
    a plan explicitly lists them."""
    plan = make_plan("Starter", {})
    user = make_user()
    session = make_session([make_tenant(plan.id), plan])
    guard = check_subscription_tier("ai_responses")

    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=user, session=session)

    assert exc_info.value.status_code == 402


@pytest.mark.asyncio
async def test_tenant_with_no_plan_returns_402():
    user = make_user()
    session = make_session([make_tenant(None)])
    guard = check_subscription_tier("multi_branch")

    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=user, session=session)

    assert exc_info.value.status_code == 402
    assert exc_info.value.detail["error"]["current_plan"] is None
