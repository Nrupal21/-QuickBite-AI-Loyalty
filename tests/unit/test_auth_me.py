"""Unit tests for AuthService.me — the GET /auth/me payload (AUTH-04).

This is what the dashboard reads to decide which controls to render. It must
report the role faithfully and decrypt PII for display without ever exposing
the password hash or TOTP secret.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.encryption import encrypt_pii, sha256_hex
from app.db.models.user import Role, User
from app.services.auth_service import AuthService

TENANT_ID = uuid.uuid4()
ROLE_ID = uuid.uuid4()
EMAIL = "manager@marcos.in"


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
        "encrypted_username": None,
        "hashed_password": "$2b$12$notarealhash",
        "mfa_enabled": True,
        "totp_secret": encrypt_pii("SECRETSECRETSECR"),
        "email_verified": True,
        "is_active": True,
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.id = uuid.uuid4()
    return user


@pytest.mark.asyncio
async def test_me_reports_role_level_and_permissions():
    user = make_user()
    role = Role(
        name="MANAGER",
        level=3,
        permissions={"manage_loyalty": True, "approve_responses": True},
        mfa_required=True,
    )
    role.id = ROLE_ID

    response = await AuthService(session=make_session([role])).me(user)

    assert response.role == "MANAGER"
    assert response.role_level == 3
    assert response.permissions == {"manage_loyalty": True, "approve_responses": True}
    assert response.mfa_required is True
    assert response.mfa_enabled is True
    assert response.tenant_id == str(TENANT_ID)


@pytest.mark.asyncio
async def test_me_decrypts_email_and_username_for_display():
    user = make_user(encrypted_username=encrypt_pii("marco.p"))
    role = Role(name="OWNER", level=2, permissions={}, mfa_required=True)
    role.id = ROLE_ID

    response = await AuthService(session=make_session([role])).me(user)

    assert response.email == EMAIL
    assert response.username == "marco.p"


@pytest.mark.asyncio
async def test_me_never_exposes_password_hash_or_totp_secret():
    user = make_user()
    role = Role(name="STAFF", level=4, permissions={"view_dashboard": True}, mfa_required=False)
    role.id = ROLE_ID

    response = await AuthService(session=make_session([role])).me(user)

    serialised = response.model_dump_json()
    assert "hashed_password" not in serialised
    assert user.hashed_password not in serialised
    assert user.totp_secret not in serialised


@pytest.mark.asyncio
async def test_me_handles_a_role_with_no_permissions_map():
    """permissions is JSONB and nullable — must degrade to {}, not None."""
    user = make_user()
    role = Role(name="STAFF", level=4, permissions=None, mfa_required=False)
    role.id = ROLE_ID

    response = await AuthService(session=make_session([role])).me(user)

    assert response.permissions == {}
