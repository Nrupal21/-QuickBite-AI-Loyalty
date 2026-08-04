"""Unit tests for TOTP enrollment — AuthService.start/enroll/confirm (AUTH-04).

Enrollment exists so Doc 3's "MFA REQUIRED for Super Admin, Owner, Manager"
can be enforced at login without locking those roles out. Two properties get
the most attention:

1. The candidate secret stays in Redis until a working code proves the
   authenticator holds it — writing it to the User row early would enable MFA
   for a device that never scanned the QR.
2. An enroll-purpose session token and a verify-purpose one are not
   interchangeable, so a stolen verify token cannot overwrite a live secret.

DB session and Redis are mocked per AGENTS.md testing rules.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pyotp
import pytest
from fastapi import HTTPException

from app.core.encryption import decrypt_pii, encrypt_pii, sha256_hex
from app.db.models.audit import AuditLog
from app.db.models.user import Role, User
from app.schemas.auth import MFAConfirmRequest, MFAEnrollRequest
from app.services.auth_service import AuthService

TENANT_ID = uuid.uuid4()
ROLE_ID = uuid.uuid4()
EMAIL = "owner@marcos.in"
TOKEN = "enroll-session-token"


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
        "hashed_password": "irrelevant",
        "mfa_enabled": False,
        "totp_secret": None,
        "email_verified": True,
        "is_active": True,
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.id = uuid.uuid4()
    return user


def make_role() -> Role:
    role = Role(name="OWNER", level=2, permissions={}, mfa_required=True)
    role.id = ROLE_ID
    return role


def session_payload(user: User, purpose: str) -> str:
    return json.dumps({"user_id": str(user.id), "purpose": purpose})


def added(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


# --- start --------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_issues_an_enroll_purpose_session(mocker):
    user = make_user()
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())

    response = await AuthService(session=make_session([])).start_mfa_enrollment(user)

    key, payload = cache_set.await_args.args
    assert key == f"mfa_session:{response.mfa_session_token}"
    assert json.loads(payload) == {"user_id": str(user.id), "purpose": "enroll"}
    assert response.expires_in == 300


# --- enroll -------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_returns_secret_and_uri_without_touching_the_user_row(mocker):
    user = make_user()
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=session_payload(user, "enroll")),
    )
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    session = make_session([user])

    response = await AuthService(session=session).enroll_mfa(MFAEnrollRequest(mfa_session_token=TOKEN))

    assert response.secret
    assert response.provisioning_uri.startswith("otpauth://totp/")
    assert "QuickBite" in response.provisioning_uri
    # Nothing persisted yet — an unconfirmed enrollment must not enable MFA.
    assert user.totp_secret is None
    assert user.mfa_enabled is False
    session.commit.assert_not_awaited()
    # The candidate secret is encrypted even in Redis.
    key, stored = cache_set.await_args.args
    assert key == f"mfa_enroll_secret:{TOKEN}"
    assert stored != response.secret
    assert decrypt_pii(stored) == response.secret


@pytest.mark.asyncio
async def test_enroll_rejects_a_verify_purpose_session_token(mocker):
    """A stolen verify token must not be usable to overwrite a live TOTP secret."""
    user = make_user(mfa_enabled=True, totp_secret=encrypt_pii(pyotp.random_base32()))
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=session_payload(user, "verify")),
    )
    session = make_session([user])

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).enroll_mfa(MFAEnrollRequest(mfa_session_token=TOKEN))

    assert exc_info.value.status_code == 401
    # Same code as an expired session — never reveal which flow a token unlocks.
    assert exc_info.value.detail["error"]["code"] == "MFA_SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_enroll_with_expired_session_returns_401(mocker):
    mocker.patch("app.services.auth_service.cache_service.get", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=make_session([])).enroll_mfa(
            MFAEnrollRequest(mfa_session_token="stale")
        )

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_enroll_for_a_deactivated_user_returns_401(mocker):
    user = make_user(is_active=False)
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=session_payload(user, "enroll")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=make_session([user])).enroll_mfa(
            MFAEnrollRequest(mfa_session_token=TOKEN)
        )

    assert exc_info.value.status_code == 401


# --- confirm ------------------------------------------------------------


def patch_confirm_cache(mocker, user: User, secret: str, purpose: str = "enroll"):
    """cache_service.get() is called twice: session lookup, then the secret."""
    return mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(side_effect=[session_payload(user, purpose), encrypt_pii(secret)]),
    )


@pytest.mark.asyncio
async def test_confirm_with_valid_code_persists_secret_and_returns_tokens(mocker):
    user = make_user()
    role = make_role()
    secret = pyotp.random_base32()
    patch_confirm_cache(mocker, user, secret)
    mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())
    mocker.patch("app.services.auth_service.create_refresh_token", return_value="refresh-xyz")
    session = make_session([user, role])

    response = await AuthService(session=session).confirm_mfa(
        MFAConfirmRequest(mfa_session_token=TOKEN, totp_code=pyotp.TOTP(secret).now()), "iphash"
    )

    assert response.access_token
    assert response.refresh_token == "refresh-xyz"
    assert user.mfa_enabled is True
    # Stored encrypted, never in plaintext.
    assert user.totp_secret != secret
    assert decrypt_pii(user.totp_secret) == secret
    actions = [row.action for row in added(session, AuditLog)]
    assert "mfa_enrolled" in actions
    assert "login_success" in actions


@pytest.mark.asyncio
async def test_confirm_burns_the_code_against_replay(mocker):
    user = make_user()
    secret = pyotp.random_base32()
    patch_confirm_cache(mocker, user, secret)
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())
    code = pyotp.TOTP(secret).now()

    await AuthService(session=make_session([user, make_role()])).confirm_mfa(
        MFAConfirmRequest(mfa_session_token=TOKEN, totp_code=code), "iphash"
    )

    cache_set.assert_any_await(f"mfa_used:{user.id}:{code}", "1", ttl=90)


@pytest.mark.asyncio
async def test_confirm_with_wrong_code_leaves_mfa_disabled(mocker):
    user = make_user()
    secret = pyotp.random_base32()
    patch_confirm_cache(mocker, user, secret)
    mocker.patch("app.services.auth_service.cache_service.incr", AsyncMock(return_value=1))
    mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=make_session([user])).confirm_mfa(
            MFAConfirmRequest(mfa_session_token=TOKEN, totp_code="000000"), "iphash"
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "MFA_CODE_INVALID"
    assert exc_info.value.detail["error"]["attempts_remaining"] == 2
    assert user.mfa_enabled is False
    assert user.totp_secret is None


@pytest.mark.asyncio
async def test_confirm_third_wrong_code_drops_the_session(mocker):
    user = make_user()
    secret = pyotp.random_base32()
    patch_confirm_cache(mocker, user, secret)
    mocker.patch("app.services.auth_service.cache_service.incr", AsyncMock(return_value=3))
    delete_mock = mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=make_session([user])).confirm_mfa(
            MFAConfirmRequest(mfa_session_token=TOKEN, totp_code="000000"), "iphash"
        )

    assert exc_info.value.detail["error"]["code"] == "MFA_TOO_MANY_ATTEMPTS"
    delete_mock.assert_any_await(f"mfa_session:{TOKEN}")
    delete_mock.assert_any_await(f"mfa_enroll_secret:{TOKEN}")


@pytest.mark.asyncio
async def test_confirm_without_a_started_enrollment_returns_400(mocker):
    user = make_user()
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(side_effect=[session_payload(user, "enroll"), None]),
    )

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=make_session([user])).confirm_mfa(
            MFAConfirmRequest(mfa_session_token=TOKEN, totp_code="123456"), "iphash"
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "MFA_ENROLLMENT_NOT_STARTED"


@pytest.mark.asyncio
async def test_confirm_rejects_a_verify_purpose_session_token(mocker):
    user = make_user()
    secret = pyotp.random_base32()
    patch_confirm_cache(mocker, user, secret, purpose="verify")

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=make_session([user])).confirm_mfa(
            MFAConfirmRequest(mfa_session_token=TOKEN, totp_code=pyotp.TOTP(secret).now()),
            "iphash",
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "MFA_SESSION_EXPIRED"
