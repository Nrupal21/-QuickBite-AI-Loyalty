"""Unit tests for AuthService.verify_mfa — one per AUTH-02 MFA acceptance criterion.

DB session and Redis are mocked per AGENTS.md testing rules.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pyotp
import pytest
from fastapi import HTTPException

from app.core.encryption import encrypt_pii, sha256_hex
from app.core.security import hash_password
from app.db.models.user import Role, User
from app.schemas.auth import MFAVerify
from app.services.auth_service import AuthService

TENANT_ID = uuid.uuid4()
ROLE_ID = uuid.uuid4()
EMAIL = "owner@marcos.in"
TOTP_SECRET = pyotp.random_base32()
MFA_SESSION_TOKEN = "fake-mfa-session-token"


def verify_session_payload(user: User) -> str:
    """What login() parks in Redis for a verify-purpose MFA session (AUTH-04).

    The `purpose` field pins the token to /auth/mfa/verify — a "verify" token
    handed to /auth/mfa/enroll is rejected, so a stolen one cannot be used to
    overwrite a working TOTP secret.
    """
    return json.dumps(
        {"user_id": str(user.id), "tenant_id": str(user.tenant_id), "purpose": "verify"}
    )


@pytest.fixture(autouse=True)
def _mock_rls(mocker):
    """_resolve_mfa_session binds RLS tenant context from the session
    payload before its User lookup — mocked as a no-op so these tests only
    account for the ORM queries they reason about, same rationale as
    test_identity_link_service.py."""
    mocker.patch("app.services.auth_service.rls.set_tenant_context", AsyncMock())


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


def make_user() -> User:
    user = User(
        tenant_id=TENANT_ID,
        role_id=ROLE_ID,
        email_hash=sha256_hex(EMAIL),
        encrypted_email=encrypt_pii(EMAIL),
        hashed_password=hash_password("irrelevant-for-mfa-tests"),
        mfa_enabled=True,
        totp_secret=encrypt_pii(TOTP_SECRET),
        email_verified=True,
        is_active=True,
    )
    user.id = uuid.uuid4()
    return user


def make_role() -> Role:
    role = Role(name="OWNER", level=2, permissions={}, mfa_required=True)
    role.id = ROLE_ID
    return role


@pytest.mark.asyncio
async def test_verify_mfa_correct_code_returns_tokens(mocker):
    user = make_user()
    role = make_role()
    session = make_session([user, role])
    mocker.patch(
        "app.services.auth_service.cache_service.get", AsyncMock(return_value=verify_session_payload(user))
    )
    mocker.patch("app.services.auth_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())
    mocker.patch("app.services.auth_service.create_refresh_token", return_value="refresh-xyz")

    code = pyotp.TOTP(TOTP_SECRET).now()
    response = await AuthService(session=session).verify_mfa(
        MFAVerify(mfa_session_token=MFA_SESSION_TOKEN, totp_code=code), "iphash"
    )

    assert response.access_token
    assert response.refresh_token == "refresh-xyz"


@pytest.mark.asyncio
async def test_verify_mfa_wrong_code_returns_401_with_attempts_remaining(mocker):
    user = make_user()
    session = make_session([user])
    mocker.patch(
        "app.services.auth_service.cache_service.get", AsyncMock(return_value=verify_session_payload(user))
    )
    mocker.patch("app.services.auth_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.services.auth_service.cache_service.incr", AsyncMock(return_value=1))

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).verify_mfa(
            MFAVerify(mfa_session_token=MFA_SESSION_TOKEN, totp_code="000000"), "iphash"
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "MFA_CODE_INVALID"
    assert exc_info.value.detail["error"]["attempts_remaining"] == 2


@pytest.mark.asyncio
async def test_verify_mfa_replayed_code_rejected(mocker):
    user = make_user()
    session = make_session([user])
    mocker.patch(
        "app.services.auth_service.cache_service.get", AsyncMock(return_value=verify_session_payload(user))
    )
    mocker.patch("app.services.auth_service.cache_service.exists", AsyncMock(return_value=True))

    code = pyotp.TOTP(TOTP_SECRET).now()
    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).verify_mfa(
            MFAVerify(mfa_session_token=MFA_SESSION_TOKEN, totp_code=code), "iphash"
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "MFA_CODE_ALREADY_USED"


@pytest.mark.asyncio
async def test_verify_mfa_third_failure_forces_relogin(mocker):
    user = make_user()
    session = make_session([user])
    mocker.patch(
        "app.services.auth_service.cache_service.get", AsyncMock(return_value=verify_session_payload(user))
    )
    mocker.patch("app.services.auth_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.services.auth_service.cache_service.incr", AsyncMock(return_value=3))
    delete_mock = mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).verify_mfa(
            MFAVerify(mfa_session_token=MFA_SESSION_TOKEN, totp_code="000000"), "iphash"
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "MFA_TOO_MANY_ATTEMPTS"
    delete_mock.assert_any_await(f"mfa_session:{MFA_SESSION_TOKEN}")


@pytest.mark.asyncio
async def test_verify_mfa_missing_session_token_returns_401(mocker):
    session = make_session([])
    mocker.patch("app.services.auth_service.cache_service.get", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).verify_mfa(
            MFAVerify(mfa_session_token="expired-token", totp_code="123456"), "iphash"
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "MFA_SESSION_EXPIRED"
