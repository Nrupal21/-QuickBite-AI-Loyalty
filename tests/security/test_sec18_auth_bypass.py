"""SEC-18 — Authentication Bypass Penetration Tests.

Executes the attack cases specified in `docs/PENTEST_PLAN.md` §1 against the
real verification code path (`app/api/v1/dependencies/auth.py`), not a
re-description of it. Each test plays the attacker: it crafts the exact
malicious input described in the plan and asserts the defense actually
fires, rather than asserting the defense's internals.

Session/DB mocking follows the same pattern as `tests/unit/test_auth_login.py`
(`make_session`) and `tests/unit/test_customer_session.py` (`app.dependency_
overrides` + the real ASGI `client` for the one full-stack case, #1.4).
"""

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import HTTPException

from app.api.v1.dependencies.auth import _classify, get_current_user
from app.core.config import settings
from app.core.encryption import encrypt_pii, sha256_hex
from app.core.rbac import RoleLevel
from app.core.security import create_access_token
from app.db.base import get_db
from app.db.models.user import Role, User
from app.main import app

TENANT_ID = uuid.uuid4()


def make_user(**overrides) -> User:
    defaults = {
        "tenant_id": TENANT_ID,
        "role_id": uuid.uuid4(),
        "email_hash": sha256_hex("owner@marcos.in"),
        "encrypted_email": encrypt_pii("owner@marcos.in"),
        "hashed_password": "unused-in-these-tests",
        "mfa_enabled": False,
        "totp_secret": None,
        "email_verified": True,
        "is_active": True,
        "failed_login_count": 0,
        "locked_until": None,
        "tokens_valid_from": None,
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.id = overrides.get("id", uuid.uuid4())
    return user


def make_request(bearer_token: str) -> MagicMock:
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {bearer_token}"}
    request.state = MagicMock()
    return request


def _b64url(data: dict) -> bytes:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=")


def forge_unsigned_token(claims: dict) -> str:
    """`alg: none`, no signature segment at all — the classic JWT bypass."""
    header = _b64url({"alg": "none", "typ": "JWT"})
    payload = _b64url(claims)
    return (header + b"." + payload + b".").decode()


# --- 1.1 alg:none forgery -------------------------------------------------


@pytest.mark.asyncio
async def test_alg_none_forged_token_is_rejected():
    now = datetime.now(timezone.utc)
    forged = forge_unsigned_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": str(TENANT_ID),
            "role": "OWNER",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(forged), MagicMock())

    assert exc_info.value.status_code == 401


# --- 1.2 algorithm confusion: HS256 claiming the Supabase issuer ---------


@pytest.mark.asyncio
async def test_hs256_token_claiming_supabase_issuer_is_rejected(mocker):
    """An attacker who obtains Supabase's *public* JWKS key cannot replay it
    as an HMAC secret: `_classify` pins HS256 to the LOCAL provider only, so
    an HS256 token asserting the Supabase issuer is rejected before the
    (asymmetric-only) Supabase verifier is ever reached."""
    mocker.patch.object(settings, "SUPABASE_PROJECT_REF", "test-project")
    now = datetime.now(timezone.utc)
    confusion_token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iss": settings.supabase_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=15),
        },
        "some-guessed-or-leaked-public-key-bytes",
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as exc_info:
        _classify(confusion_token)

    assert exc_info.value.status_code == 401


# --- 1.3 expired token reuse ----------------------------------------------


@pytest.mark.asyncio
async def test_expired_local_token_is_rejected():
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": str(TENANT_ID),
            "role": "OWNER",
            "jti": str(uuid.uuid4()),
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(expired), MagicMock())

    assert exc_info.value.status_code == 401


# --- 1.4 cross-role escalation: Staff JWT on an Owner-only route ----------


@pytest.mark.asyncio
async def test_staff_jwt_on_owner_only_billing_checkout_returns_403(mocker):
    """Full ASGI round trip — the exact regression class this codebase's own
    billing.py docstring warns about ("Previously plain get_current_user —
    any Manager or Staff account could call this directly")."""
    staff_user = make_user(role_id=uuid.uuid4())
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            _scalar_result(staff_user),  # get_current_user: select(User)
            _scalar_result(True),  # get_current_user: Tenant.is_active
            _scalar_result(RoleLevel.STAFF),  # require_role: select(Role.level)
        ]
    )
    mocker.patch("app.api.v1.dependencies.auth.rls.set_tenant_context", AsyncMock())
    token = create_access_token(staff_user.id, TENANT_ID, "STAFF")

    app.dependency_overrides[get_db] = lambda: session
    try:
        response = await _post_json(
            "/api/v1/billing/checkout",
            {"plan_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "INSUFFICIENT_PERMISSIONS"


# --- 1.5 jku/x5u header injection ------------------------------------------


def test_jku_header_is_rejected():
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=5)},
        settings.SECRET_KEY,
        algorithm="HS256",
        headers={"jku": "https://attacker.example/jwks.json"},
    )

    with pytest.raises(HTTPException) as exc_info:
        _classify(token)

    assert exc_info.value.status_code == 401


# --- 1.7 TOTP replay -------------------------------------------------------


@pytest.mark.asyncio
async def test_totp_code_replay_within_window_is_rejected(mocker):
    """Same valid code submitted twice to /auth/mfa/verify: 1st succeeds,
    2nd must 401 even though the code itself is still numerically valid."""
    from app.schemas.auth import MFAVerify
    from app.services.auth_service import AuthService

    user = make_user(totp_secret=encrypt_pii("JBSWY3DPEHPK3PXP"))
    role = Role(name="OWNER", level=RoleLevel.OWNER, permissions={}, mfa_required=False)
    role.id = user.role_id
    session = MagicMock()
    session.commit = AsyncMock()
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(
            return_value=json.dumps(
                {"user_id": str(user.id), "tenant_id": str(TENANT_ID), "purpose": "verify"}
            )
        ),
    )
    mocker.patch("app.services.auth_service.rls.set_tenant_context", AsyncMock())
    # 1st execute: _resolve_mfa_session's select(User) (call #1, succeeds).
    # 2nd: issue_tokens's select(Role) via _get_role_by_id (call #1 only).
    # 3rd: _resolve_mfa_session's select(User) again for call #2 (the
    # replay), which then 401s on the `exists` check before ever reaching
    # issue_tokens.
    session.execute = AsyncMock(
        side_effect=[_scalar_result(user), _scalar_result(role), _scalar_result(user)]
    )
    mocker.patch("app.services.auth_service.verify_totp_code", return_value=True)
    mocker.patch(
        "app.services.auth_service.create_refresh_token", return_value="refresh-abc"
    )
    # First call: not yet used. Second call: the replay key now exists.
    exists_mock = mocker.patch(
        "app.services.auth_service.cache_service.exists", AsyncMock(side_effect=[False, True])
    )
    mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())

    request = MFAVerify(mfa_session_token="session-token-abc", totp_code="482913")
    service = AuthService(session=session)

    first = await service.verify_mfa(request, "iphash")
    assert first.access_token

    with pytest.raises(HTTPException) as exc_info:
        await service.verify_mfa(request, "iphash")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "MFA_CODE_ALREADY_USED"
    assert exists_mock.await_count == 2


# --- 7.4 missing `iat` claim -------------------------------------------------


@pytest.mark.asyncio
async def test_missing_iat_claim_is_rejected(mocker):
    """An otherwise-valid signed token with no `iat` at all must fail closed
    in `_assert_not_globally_revoked` rather than skip the revocation check
    for lack of anything to compare against. No existing test covers this
    specific fail-closed branch."""
    mocker.patch("app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.api.v1.dependencies.auth.rls.set_tenant_context", AsyncMock())
    user = make_user()
    token = jwt.encode(
        {
            "sub": str(user.id),
            "tenant_id": str(TENANT_ID),
            "role": "OWNER",
            "jti": str(uuid.uuid4()),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
            # no "iat" claim at all
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_scalar_result(user), _scalar_result(True)])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(token), session)

    assert exc_info.value.status_code == 401


# --- 1.6 force-logout bypass -------------------------------------------------


@pytest.mark.asyncio
async def test_token_issued_before_force_logout_watermark_is_rejected(mocker):
    """An admin's force-logout bumps `tokens_valid_from` into the future
    relative to an already-issued token's `iat`; that token must be rejected
    on its very next use via the standard REST `get_current_user` path, not
    only via the WebSocket-specific check `tests/unit/test_dashboard_router.py`
    already covers."""
    mocker.patch("app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.api.v1.dependencies.auth.rls.set_tenant_context", AsyncMock())
    user = make_user(tokens_valid_from=datetime.now(timezone.utc) + timedelta(hours=1))
    stale_token = jwt.encode(
        {
            "sub": str(user.id),
            "tenant_id": str(TENANT_ID),
            "role": "OWNER",
            "jti": str(uuid.uuid4()),
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_scalar_result(user), _scalar_result(True)])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(stale_token), session)

    assert exc_info.value.status_code == 401


# --- 6.1 tenant_id claim tampering -------------------------------------------


@pytest.mark.asyncio
async def test_tenant_id_claim_tamper_finds_no_row_not_cross_tenant_access(mocker):
    """Even a correctly-signed token whose `tenant_id` claim names a
    different tenant than the user's real row must not grant that tenant's
    data — authorisation always comes from `user.tenant_id` on the loaded
    row (the `Principal` invariant documented in this module's docstring),
    never from the claim. Simulated as: the RLS-scoped lookup under the
    *claimed* tenant returns no row, because the user does not actually
    belong to it."""
    mocker.patch("app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.api.v1.dependencies.auth.rls.set_tenant_context", AsyncMock())
    user = make_user()
    other_tenant_id = uuid.uuid4()
    tampered = jwt.encode(
        {
            "sub": str(user.id),
            "tenant_id": str(other_tenant_id),
            "role": "OWNER",
            "jti": str(uuid.uuid4()),
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    session = MagicMock()
    # select(User), scoped to the claimed (wrong) tenant, finds nothing.
    session.execute = AsyncMock(side_effect=[_scalar_result(None)])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(tampered), session)

    assert exc_info.value.status_code == 401


# --- helpers ---------------------------------------------------------------


def _scalar_result(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar_one.return_value = value
    return result


async def _post_json(url: str, json_body: dict, headers: dict):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post(url, json=json_body, headers=headers)
