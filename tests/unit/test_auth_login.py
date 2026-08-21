"""Unit tests for AuthService.login — one per AUTH-02 login acceptance criterion.

DB session and Redis are mocked per AGENTS.md testing rules.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.encryption import encrypt_pii, sha256_hex
from app.core.security import hash_password
from app.db.models.audit import AuditLog
from app.db.models.user import Role, User
from app.schemas.auth import MFAChallengeResponse, MFAEnrollmentRequiredResponse, UserLogin
from app.services.auth_service import AuthService

TENANT_ID = uuid.uuid4()
ROLE_ID = uuid.uuid4()
EMAIL = "owner@marcos.in"
PASSWORD = "korma-monsoon-49-bicycle"


def make_session(execute_results: list) -> MagicMock:
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
        "hashed_password": hash_password(PASSWORD),
        "mfa_enabled": False,
        "totp_secret": None,
        "email_verified": True,
        # Column defaults only apply on flush; these instances are never
        # persisted, so an unset is_active would read as None (falsy) and every
        # login test would trip the deactivated-account guard.
        "is_active": True,
        "failed_login_count": 0,
        "locked_until": None,
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.id = uuid.uuid4()
    return user


def make_role(name: str = "STAFF", level: int = 4, mfa_required: bool = False) -> Role:
    """Defaults to STAFF because it is the one seeded role where Doc 3 makes MFA
    optional — the only role that can reach a token pair in a single step."""
    role = Role(name=name, level=level, permissions={}, mfa_required=mfa_required)
    role.id = ROLE_ID
    return role


def added_instances(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


@pytest.fixture(autouse=True)
def resolved_tenant(mocker):
    """Stub the credential->tenant resolvers and the RLS bind.

    Login now resolves which tenant owns an identifier *before* reading the
    user row, because under row-level security an unscoped `select(User)`
    returns nothing (migration 0007). Both steps issue their own
    `session.execute`, so stubbing them keeps `make_session`'s result list
    describing only the ORM queries each test actually reasons about.
    """
    mocker.patch(
        "app.services.auth_service.bootstrap.tenant_for_user_email_hash",
        AsyncMock(return_value=TENANT_ID),
    )
    mocker.patch(
        "app.services.auth_service.bootstrap.tenant_for_user_username_hash",
        AsyncMock(return_value=TENANT_ID),
    )
    mocker.patch("app.services.auth_service.rls.set_tenant_context", AsyncMock())


@pytest.mark.asyncio
async def test_login_correct_password_no_mfa_returns_tokens(mocker):
    user = make_user(mfa_enabled=False)
    role = make_role()  # STAFF — MFA optional, so no challenge blocks the token pair
    session = make_session([user, role])
    mocker.patch(
        "app.services.auth_service.create_refresh_token", return_value="refresh-token-abc"
    )

    response = await AuthService(session=session).login(
        UserLogin(identifier=EMAIL, password=PASSWORD), "iphash"
    )

    assert response.access_token
    assert response.refresh_token == "refresh-token-abc"
    assert response.role == "STAFF"
    assert response.tenant_id == str(TENANT_ID)
    audit_rows = added_instances(session, AuditLog)
    assert audit_rows[-1].action == "login_success"


@pytest.mark.asyncio
async def test_login_by_username_resolves_the_same_account(mocker):
    """/auth/identify matches staff on username, so /auth/login must accept one too."""
    user = make_user(mfa_enabled=False, username_hash=sha256_hex("marco.p"))
    role = make_role()
    session = make_session([user, role])
    mocker.patch("app.services.auth_service.create_refresh_token", return_value="refresh-abc")

    response = await AuthService(session=session).login(
        UserLogin(identifier="marco.p", password=PASSWORD), "iphash"
    )

    assert response.access_token
    # Matched on username_hash, not email_hash.
    where_clause = str(session.execute.await_args_list[0].args[0])
    assert "username_hash" in where_clause


@pytest.mark.asyncio
async def test_login_binds_tenant_before_reading_the_user_row(mocker):
    """Under RLS the row is invisible until the tenant is bound, so the bind
    must happen first — not as a tidy-up after the read."""
    user = make_user(mfa_enabled=False)
    session = make_session([user, make_role()])
    mocker.patch("app.services.auth_service.create_refresh_token", return_value="r")
    set_tenant = mocker.patch("app.services.auth_service.rls.set_tenant_context", AsyncMock())

    await AuthService(session=session).login(
        UserLogin(identifier=EMAIL, password=PASSWORD), "iphash"
    )

    set_tenant.assert_awaited()
    assert set_tenant.await_args.args[1] == TENANT_ID


@pytest.mark.asyncio
async def test_login_unknown_identifier_falls_back_to_null_tenant_lookup(mocker):
    """No tenant owns the credential — but that's also what a standard user's
    (tenant_id IS NULL) identifier looks like from the bootstrap resolver's
    single-uuid return, so login() always tries a direct null-tenant lookup
    before giving up (see _get_user_by_identifier_hash). For a truly unknown
    identifier that lookup also finds nothing, and login still ends in a
    generic 401 — the caller cannot tell this from a wrong password."""
    mocker.patch(
        "app.services.auth_service.bootstrap.tenant_for_user_email_hash",
        AsyncMock(return_value=None),
    )
    session = make_session([None])  # the null-tenant fallback query finds nobody

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).login(
            UserLogin(identifier="nobody@marcos.in", password=PASSWORD), "iphash"
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"
    session.execute.assert_awaited_once()  # the null-tenant fallback, and nothing else


@pytest.mark.asyncio
async def test_login_rejects_phone_shaped_identifier():
    """users.phone_hash is not unique, so a phone cannot identify one account."""
    session = make_session([])

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).login(
            UserLogin(identifier="+919876543210", password=PASSWORD), "iphash"
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "IDENTIFIER_INVALID"


@pytest.mark.asyncio
async def test_login_correct_password_mfa_enabled_returns_challenge(mocker):
    user = make_user(mfa_enabled=True)
    role = make_role()
    session = make_session([user, role])
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())

    response = await AuthService(session=session).login(
        UserLogin(identifier=EMAIL, password=PASSWORD), "iphash"
    )

    assert isinstance(response, MFAChallengeResponse)
    assert response.status == "mfa_required"
    assert response.expires_in == 300
    key, payload = cache_set.await_args.args
    assert key == f"mfa_session:{response.mfa_session_token}"
    assert json.loads(payload) == {
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "purpose": "verify",
    }


@pytest.mark.asyncio
async def test_login_owner_without_totp_gets_enrollment_challenge_not_tokens(mocker):
    """Doc 3 marks MFA REQUIRED for Owner — a token pair here would be single-factor."""
    user = make_user(mfa_enabled=False)
    role = make_role(name="OWNER", level=2, mfa_required=True)
    session = make_session([user, role])
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())

    response = await AuthService(session=session).login(
        UserLogin(identifier=EMAIL, password=PASSWORD), "iphash"
    )

    assert isinstance(response, MFAEnrollmentRequiredResponse)
    assert response.status == "mfa_enrollment_required"
    assert response.role == "OWNER"
    assert not hasattr(response, "access_token")
    _, payload = cache_set.await_args.args
    assert json.loads(payload)["purpose"] == "enroll"
    audit_rows = added_instances(session, AuditLog)
    assert audit_rows[-1].action == "mfa_enrollment_required"


@pytest.mark.asyncio
async def test_login_manager_without_totp_gets_enrollment_challenge(mocker):
    user = make_user(mfa_enabled=False)
    role = make_role(name="MANAGER", level=3, mfa_required=True)
    session = make_session([user, role])
    mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())

    response = await AuthService(session=session).login(
        UserLogin(identifier=EMAIL, password=PASSWORD), "iphash"
    )

    assert isinstance(response, MFAEnrollmentRequiredResponse)
    assert response.role == "MANAGER"


@pytest.mark.asyncio
async def test_login_standard_user_issues_tokens_with_null_tenant(mocker):
    """A standard user (role USER, tenant_id NULL) resolves via the
    null-tenant fallback in _get_user_by_identifier_hash, and gets a real
    token pair — just with no tenant to scope, since USER's mfa_required is
    False and there is nothing to challenge."""
    mocker.patch(
        "app.services.auth_service.bootstrap.tenant_for_user_email_hash",
        AsyncMock(return_value=None),
    )
    user = make_user(tenant_id=None, mfa_enabled=False)
    role = make_role(name="USER", level=6, mfa_required=False)
    # execute calls: null-tenant fallback lookup -> user, role lookup
    session = make_session([user, role])
    mocker.patch("app.services.auth_service.create_refresh_token", return_value="refresh-std")

    response = await AuthService(session=session).login(
        UserLogin(identifier=EMAIL, password=PASSWORD), "iphash"
    )

    assert response.access_token
    assert response.role == "USER"
    assert response.tenant_id is None


@pytest.mark.asyncio
async def test_login_deactivated_member_returns_403_after_password_check():
    """Checked after the password so it cannot enumerate deactivated accounts."""
    user = make_user(is_active=False)
    session = make_session([user])

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).login(
            UserLogin(identifier=EMAIL, password=PASSWORD), "iphash"
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "ACCOUNT_DEACTIVATED"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401_generic_and_increments_count():
    user = make_user(failed_login_count=2)
    session = make_session([user])

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).login(
            UserLogin(identifier=EMAIL, password="wrong-password"), "iphash"
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"
    assert user.failed_login_count == 3
    audit_rows = added_instances(session, AuditLog)
    assert audit_rows[-1].action == "login_failed"


@pytest.mark.asyncio
async def test_login_unknown_email_returns_401_generic_no_timing_oracle():
    session = make_session([None])

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).login(
            UserLogin(identifier="nobody@marcos.in", password="whatever12345"), "iphash"
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"
    session.commit.assert_not_awaited()  # nothing to persist for an unknown email


@pytest.mark.asyncio
async def test_login_tenth_failure_locks_account_and_sends_email(mocker):
    user = make_user(failed_login_count=9)
    session = make_session([user])
    send_email = mocker.patch(
        "app.services.auth_service.messaging_service.send_account_locked_email",
        AsyncMock(return_value=True),
    )

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).login(
            UserLogin(identifier=EMAIL, password="wrong-password"), "iphash"
        )

    assert exc_info.value.status_code == 423
    assert exc_info.value.detail["error"]["code"] == "ACCOUNT_LOCKED"
    assert user.failed_login_count == 10
    assert user.locked_until > datetime.now(timezone.utc)
    send_email.assert_awaited_once()
    audit_rows = added_instances(session, AuditLog)
    assert audit_rows[-1].action == "account_locked"


@pytest.mark.asyncio
async def test_login_already_locked_account_returns_423_even_with_correct_password():
    user = make_user(locked_until=datetime.now(timezone.utc) + timedelta(minutes=30))
    session = make_session([user])

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).login(
            UserLogin(identifier=EMAIL, password=PASSWORD), "iphash"
        )

    assert exc_info.value.status_code == 423
    assert exc_info.value.detail["error"]["code"] == "ACCOUNT_LOCKED"


@pytest.mark.asyncio
async def test_login_unverified_email_returns_403():
    user = make_user(email_verified=False)
    session = make_session([user])

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).login(
            UserLogin(identifier=EMAIL, password=PASSWORD), "iphash"
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "EMAIL_NOT_VERIFIED"
