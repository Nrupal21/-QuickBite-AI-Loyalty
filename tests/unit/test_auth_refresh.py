"""Unit tests for AuthService refresh/logout/logout-all — one per AUTH-03 acceptance criterion.

DB session and Redis are mocked per AGENTS.md testing rules.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.encryption import sha256_hex
from app.db.models.audit import AuditLog
from app.db.models.user import Role, Session, User
from app.schemas.auth import RefreshRequest
from app.services.auth_service import AuthService

TENANT_ID = uuid.uuid4()
ROLE_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def make_scalar_result(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def make_scalars_result(values: list) -> MagicMock:
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = values
    result.scalars.return_value = scalars_mock
    return result


def make_db_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_results)
    return session


@pytest.fixture(autouse=True)
def resolved_tenant(mocker):
    """Stub the refresh-token->tenant resolver and the RLS bind.

    A refresh token is opaque and carries no claims, so the session row is the
    only thing that knows its tenant — under RLS it must be resolved (migration
    0007) and bound before the row is readable. Both steps issue their own
    session.execute, so stubbing them keeps make_db_session's result list
    describing only the ORM queries these tests reason about.
    """
    mocker.patch(
        "app.services.auth_service.bootstrap.tenant_for_session_token_hash",
        AsyncMock(return_value=TENANT_ID),
    )
    mocker.patch("app.services.auth_service.rls.set_tenant_context", AsyncMock())


def make_session_row(*, token: str, revoked: bool = False, expires_in_days: int = 30) -> Session:
    row = Session(
        user_id=USER_ID,
        tenant_id=TENANT_ID,
        refresh_token_hash=sha256_hex(token),
        ip_address_hash="iphash",
        user_agent="pytest-agent",
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
        revoked=revoked,
    )
    row.id = uuid.uuid4()
    return row


def make_user() -> User:
    user = User(
        tenant_id=TENANT_ID,
        role_id=ROLE_ID,
        email_hash="hash",
        encrypted_email="enc",
        hashed_password="hash",
        mfa_enabled=False,
        email_verified=True,
    )
    user.id = USER_ID
    return user


def make_role() -> Role:
    role = Role(name="OWNER", level=2, permissions={}, mfa_required=True)
    role.id = ROLE_ID
    return role


@pytest.mark.asyncio
async def test_refresh_valid_token_rotates_and_returns_new_pair(mocker):
    old_row = make_session_row(token="old-refresh-token")
    user = make_user()
    role = make_role()
    db_session = make_db_session(
        [make_scalar_result(old_row), make_scalar_result(user), make_scalar_result(role)]
    )
    mocker.patch(
        "app.services.auth_service.create_refresh_token", return_value="new-refresh-token"
    )

    response = await AuthService(session=db_session).refresh(
        RefreshRequest(refresh_token="old-refresh-token"), "iphash", "pytest-agent"
    )

    assert response.refresh_token == "new-refresh-token"
    assert old_row.revoked is True  # old token is single-use, rotated immediately
    new_sessions = [
        call.args[0] for call in db_session.add.call_args_list if isinstance(call.args[0], Session)
    ]
    assert len(new_sessions) == 1
    assert new_sessions[0].refresh_token_hash == sha256_hex("new-refresh-token")


@pytest.mark.asyncio
async def test_refresh_replayed_old_token_revokes_all_sessions():
    replayed_row = make_session_row(token="already-used-token", revoked=True)
    other_active_row = make_session_row(token="other-active-token", revoked=False)

    db_session = make_db_session(
        [
            make_scalar_result(replayed_row),
            make_scalars_result([replayed_row, other_active_row]),
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=db_session).refresh(
            RefreshRequest(refresh_token="already-used-token"), "iphash", "pytest-agent"
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "REFRESH_TOKEN_INVALID"
    assert other_active_row.revoked is True  # every session for the user is revoked
    audit_rows = [
        call.args[0] for call in db_session.add.call_args_list if isinstance(call.args[0], AuditLog)
    ]
    assert audit_rows[0].action == "refresh_token_replay_detected"


@pytest.mark.asyncio
async def test_refresh_unknown_token_returns_401():
    db_session = make_db_session([make_scalar_result(None)])

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=db_session).refresh(
            RefreshRequest(refresh_token="never-issued-token"), "iphash", "pytest-agent"
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "REFRESH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_logout_blocklists_jti_and_revokes_session(mocker):
    session_row = make_session_row(token="active-refresh-token")
    # Second result backs the tokens_valid_from UPDATE — logout also revokes
    # any Supabase/Firebase session linked to this user, since neither
    # provider exposes a server-side per-session handle to narrow that to.
    db_session = make_db_session([make_scalar_result(session_row), MagicMock()])
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())

    exp = datetime.now(timezone.utc) + timedelta(minutes=10)
    response = await AuthService(session=db_session).logout(
        jti="jti-123", exp=exp, refresh_token="active-refresh-token"
    )

    assert response.status == "logged_out"
    assert session_row.revoked is True
    key, value = cache_set.await_args.args
    assert key == "revoked_jti:jti-123"
    assert value == "1"
    assert cache_set.await_args.kwargs["ttl"] > 0

    update_call = db_session.execute.await_args_list[1]
    assert "tokens_valid_from" in str(update_call.args[0])


@pytest.mark.asyncio
async def test_logout_all_revokes_every_session_for_user():
    row_a = make_session_row(token="token-a")
    row_b = make_session_row(token="token-b")
    # Second result backs the tokens_valid_from UPDATE (see logout, above).
    db_session = make_db_session([make_scalars_result([row_a, row_b]), MagicMock()])

    response = await AuthService(session=db_session).logout_all(USER_ID)

    assert response.status == "logged_out"
    assert row_a.revoked is True
    assert row_b.revoked is True

    update_call = db_session.execute.await_args_list[1]
    assert "tokens_valid_from" in str(update_call.args[0])
