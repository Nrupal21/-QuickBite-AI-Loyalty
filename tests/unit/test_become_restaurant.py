"""Unit tests for AuthService.become_restaurant — a standard user's one-time
upgrade to Owner (POST /auth/register-restaurant).

DB session and Redis are mocked per AGENTS.md testing rules. _bump_tokens_valid_from
and _revoke_all_sessions are stubbed directly rather than modelled through
session.execute: their own correctness is already covered by
test_auth_refresh.py/test_admin_service.py's logout_all tests, and modelling
their Session-row query shapes here would only restate that coverage.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.encryption import encrypt_pii, sha256_hex
from app.db.models.static_data import BusinessCategory
from app.db.models.subscription import SubscriptionPlan
from app.db.models.user import Role, User
from app.schemas.auth import BecomeRestaurantRequest, MFAEnrollmentRequiredResponse
from app.services.auth_service import AuthService

EMAIL = "standard@marcos.in"
PLAN_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
PHONE = "+15550001234"
SECOND_EMAIL = "hello@marcospizzeria.in"
CONTACT_TOKEN = "test-contact-verification-token"


def contact_verification_payload(
    user: User, contact: str = PHONE, contact_type: str = "phone"
) -> str:
    """Redis payload a real verify_contact_otp() would have stored for `token`."""
    return json.dumps(
        {
            "user_id": str(user.id),
            "contact_type": contact_type,
            "contact_hash": sha256_hex(contact),
            "encrypted_contact": encrypt_pii(contact),
        }
    )


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    results = []
    for value in execute_results:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)
    session.execute = AsyncMock(side_effect=results)
    return session


def make_standard_user(**overrides) -> User:
    """A verified USER-role account with no tenant yet."""
    defaults = {
        "tenant_id": None,
        "role_id": uuid.uuid4(),
        "email_hash": sha256_hex(EMAIL),
        "encrypted_email": encrypt_pii(EMAIL),
        "hashed_password": "$2b$12$fakehashfakehashfakehash",
        "mfa_enabled": False,
        "totp_secret": None,
        "email_verified": True,
        "is_active": True,
        "failed_login_count": 0,
        "locked_until": None,
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.id = uuid.uuid4()
    return user


def make_owner_role(mfa_required: bool = True) -> Role:
    role = Role(name="OWNER", level=2, permissions={}, mfa_required=mfa_required)
    role.id = uuid.uuid4()
    return role


def make_category() -> BusinessCategory:
    category = BusinessCategory(
        slug="restaurant",
        display_name="Restaurant",
        tagline="Dine-in tables, a full menu, a regular crowd",
        icon_key="plate",
        sort_order=10,
        is_active=True,
    )
    category.id = CATEGORY_ID
    return category


def make_plan() -> SubscriptionPlan:
    plan = SubscriptionPlan(
        name="pro", display_name="Pro", price_monthly_inr=299900,
        provider_plan_id=None, feature_limits={}, trial_days=14, is_active=True,
    )
    plan.id = PLAN_ID
    return plan


def make_request(**overrides) -> BecomeRestaurantRequest:
    data = {
        "restaurant_name": "Marcos Pizzeria",
        "category_id": CATEGORY_ID,
        "plan_id": PLAN_ID,
        "contact_verification_token": CONTACT_TOKEN,
    }
    data.update(overrides)
    return BecomeRestaurantRequest(**data)


@pytest.fixture(autouse=True)
def _mock_side_effects(mocker):
    mocker.patch("app.services.auth_service.rls.set_tenant_context", AsyncMock())
    mocker.patch(
        "app.services.auth_service.AuthService._bump_tokens_valid_from", AsyncMock()
    )
    mocker.patch(
        "app.services.auth_service.AuthService._revoke_all_sessions", AsyncMock()
    )
    mocker.patch("app.services.auth_service.create_refresh_token", return_value="new-refresh")
    mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())
    mocker.patch(
        "app.services.auth_service.messaging_service.send_restaurant_joined_email",
        AsyncMock(return_value=True),
    )
    mocker.patch(
        "app.services.auth_service.messaging_service.send_restaurant_joined_sms",
        AsyncMock(return_value=True),
    )


@pytest.mark.asyncio
async def test_already_has_tenant_returns_409(mocker):
    user = make_standard_user(tenant_id=uuid.uuid4())
    session = make_session([])

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).become_restaurant(user, make_request(), "iphash")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "ALREADY_HAS_RESTAURANT"
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_plan_returns_404(mocker):
    user = make_standard_user()
    # Category resolves, then _get_active_plan's lookup finds nothing.
    session = make_session([make_category(), None])
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=contact_verification_payload(user)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).become_restaurant(user, make_request(), "iphash")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "PLAN_NOT_FOUND"


@pytest.mark.asyncio
async def test_contact_verification_expired_returns_400(mocker):
    user = make_standard_user()
    session = make_session([])
    mocker.patch("app.services.auth_service.cache_service.get", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).become_restaurant(user, make_request(), "iphash")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "CONTACT_VERIFICATION_EXPIRED"
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_contact_verification_token_for_another_user_returns_400(mocker):
    user = make_standard_user()
    other_user = make_standard_user()
    session = make_session([])
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=contact_verification_payload(other_user)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).become_restaurant(user, make_request(), "iphash")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "CONTACT_VERIFICATION_EXPIRED"


@pytest.mark.asyncio
async def test_owner_mfa_required_and_not_enrolled_withholds_tokens(mocker):
    """Doc 3: MFA is REQUIRED for Owner — an upgrade must not hand out a full
    session before enrollment, exactly like login() would refuse to."""
    user = make_standard_user(mfa_enabled=False)
    owner_role = make_owner_role(mfa_required=True)
    category = make_category()
    plan = make_plan()
    # execute calls: category, plan, owner role, subdomain-free check
    session = make_session([category, plan, owner_role, None])
    mocker.patch(
        "app.services.auth_service.generate_mfa_session_token", return_value="mfa-token-xyz"
    )
    mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=contact_verification_payload(user)),
    )

    response = await AuthService(session=session).become_restaurant(
        user, make_request(), "iphash"
    )

    assert isinstance(response, MFAEnrollmentRequiredResponse)
    assert response.status == "mfa_enrollment_required"
    assert response.role == "OWNER"
    # The upgrade itself (tenant + role) still committed — only tokens are withheld.
    assert user.tenant_id is not None
    assert user.role_id == owner_role.id
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_creates_tenant_promotes_role_and_issues_tokens(mocker):
    user = make_standard_user(mfa_enabled=True)  # MFA already enrolled, no challenge
    owner_role = make_owner_role(mfa_required=True)
    category = make_category()
    plan = make_plan()
    session = make_session([category, plan, owner_role, None])
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=contact_verification_payload(user)),
    )

    response = await AuthService(session=session).become_restaurant(
        user, make_request(), "iphash"
    )

    assert response.status == "restaurant_created"
    assert response.role == "OWNER"
    assert response.access_token
    assert response.refresh_token == "new-refresh"
    assert user.tenant_id is not None
    assert str(user.tenant_id) == response.tenant_id
    assert user.role_id == owner_role.id
    assert user.phone_hash == sha256_hex(PHONE)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_revokes_the_stale_tenant_less_token(mocker):
    user = make_standard_user(mfa_enabled=True)
    owner_role = make_owner_role(mfa_required=False)
    category = make_category()
    plan = make_plan()
    session = make_session([category, plan, owner_role, None])
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=contact_verification_payload(user)),
    )

    await AuthService(session=session).become_restaurant(user, make_request(), "iphash")

    AuthService._bump_tokens_valid_from.assert_awaited_once_with(user.id)
    AuthService._revoke_all_sessions.assert_awaited_once_with(user.id)


@pytest.mark.asyncio
async def test_unknown_category_returns_404(mocker):
    """The category is validated, not trusted.

    The plan list the caller chose from was filtered by this category, so a
    submission naming a category that does not exist would record a tenant on
    a plan its own category never offered — and nothing downstream would ever
    notice. Checked before the plan, matching the order the form asks in.
    """
    user = make_standard_user()
    session = make_session([None])
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=contact_verification_payload(user)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).become_restaurant(user, make_request(), "iphash")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "CATEGORY_NOT_FOUND"


@pytest.mark.asyncio
async def test_success_records_category_and_plan_on_the_tenant(mocker):
    user = make_standard_user(mfa_enabled=True)
    owner_role = make_owner_role(mfa_required=True)
    category = make_category()
    plan = make_plan()
    session = make_session([category, plan, owner_role, None])
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=contact_verification_payload(user)),
    )

    await AuthService(session=session).become_restaurant(user, make_request(), "iphash")

    tenant = next(
        call.args[0]
        for call in session.add.call_args_list
        if type(call.args[0]).__name__ == "Tenant"
    )
    assert tenant.category_id == CATEGORY_ID
    assert tenant.plan_id == PLAN_ID


@pytest.mark.asyncio
async def test_verified_email_contact_fills_the_email_fields(mocker):
    """A phone-registered account acquires its email here, not its phone.

    The second contact method is whichever one the account was missing. An
    implementation that always writes phone_hash would overwrite a
    phone-registered user's own login identifier with a hash of their email
    address, locking them out of the code they signed up with.
    """
    user = make_standard_user(
        mfa_enabled=True, email_hash=None, encrypted_email=None,
        phone_hash=sha256_hex(PHONE), encrypted_phone=encrypt_pii(PHONE),
    )
    owner_role = make_owner_role(mfa_required=True)
    category = make_category()
    plan = make_plan()
    session = make_session([category, plan, owner_role, None])
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(
            return_value=contact_verification_payload(
                user, contact=SECOND_EMAIL, contact_type="email"
            )
        ),
    )

    await AuthService(session=session).become_restaurant(user, make_request(), "iphash")

    assert user.email_hash == sha256_hex(SECOND_EMAIL)
    assert user.encrypted_email is not None
    # Their original login identifier is untouched.
    assert user.phone_hash == sha256_hex(PHONE)
