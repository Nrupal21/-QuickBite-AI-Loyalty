"""Unit tests for the dashboard WebSocket handshake (DASH-01).

`_authenticate_websocket` is hand-rolled (see dashboard.py's module
docstring for why `Depends(get_current_user)` doesn't fit a WebSocket
route), so it gets its own coverage mirroring what AUTH-04's tests already
assert for the HTTP path: missing/invalid/revoked token, inactive user,
and the tokens_valid_from watermark.

`rls.set_tenant_context` is stubbed to a no-op, same rationale as
test_identity_link_service.py — it is AUTH-04/TENANT-02's own contract, not
this endpoint's.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.routers.dashboard import _authenticate_websocket
from app.core.security import create_access_token
from app.db.models.user import User

TENANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def make_user(**overrides) -> User:
    defaults = {
        "tenant_id": TENANT_ID,
        "role_id": uuid.uuid4(),
        "is_active": True,
        "tokens_valid_from": datetime.now(UTC) - timedelta(days=1),
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.id = USER_ID
    return user


def make_websocket(token: str | None) -> MagicMock:
    ws = MagicMock()
    ws.query_params = {"token": token} if token is not None else {}
    ws.close = AsyncMock()
    return ws


def make_session(user: User | None) -> MagicMock:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.fixture(autouse=True)
def _mock_rls(mocker):
    mocker.patch("app.api.v1.routers.dashboard.rls.set_tenant_context", AsyncMock())


@pytest.fixture(autouse=True)
def _mock_jti_blocklist(mocker):
    mocker.patch("app.api.v1.routers.dashboard.cache_service.exists", AsyncMock(return_value=False))


@pytest.mark.asyncio
async def test_missing_token_closes_socket():
    ws = make_websocket(None)
    session = make_session(None)

    result = await _authenticate_websocket(ws, session)

    assert result is None
    ws.close.assert_awaited_once_with(code=4401)


@pytest.mark.asyncio
async def test_garbage_token_closes_socket():
    ws = make_websocket("not-a-real-jwt")
    session = make_session(None)

    result = await _authenticate_websocket(ws, session)

    assert result is None
    ws.close.assert_awaited_once_with(code=4401)


@pytest.mark.asyncio
async def test_revoked_jti_closes_socket(mocker):
    mocker.patch("app.api.v1.routers.dashboard.cache_service.exists", AsyncMock(return_value=True))
    token = create_access_token(USER_ID, TENANT_ID, "STAFF")
    ws = make_websocket(token)
    session = make_session(make_user())

    result = await _authenticate_websocket(ws, session)

    assert result is None
    ws.close.assert_awaited_once_with(code=4401)


@pytest.mark.asyncio
async def test_unknown_user_closes_socket():
    token = create_access_token(USER_ID, TENANT_ID, "STAFF")
    ws = make_websocket(token)
    session = make_session(None)

    result = await _authenticate_websocket(ws, session)

    assert result is None
    ws.close.assert_awaited_once_with(code=4401)


@pytest.mark.asyncio
async def test_inactive_user_closes_socket():
    token = create_access_token(USER_ID, TENANT_ID, "STAFF")
    ws = make_websocket(token)
    session = make_session(make_user(is_active=False))

    result = await _authenticate_websocket(ws, session)

    assert result is None
    ws.close.assert_awaited_once_with(code=4401)


@pytest.mark.asyncio
async def test_token_issued_before_revocation_watermark_closes_socket():
    """A logout-all/force-logout must reject a still-unexpired token on this
    endpoint too, the same guarantee _assert_not_globally_revoked gives the
    HTTP path."""
    token = create_access_token(USER_ID, TENANT_ID, "STAFF")
    ws = make_websocket(token)
    # Watermark bumped to the future relative to the token's iat.
    user = make_user(tokens_valid_from=datetime.now(UTC) + timedelta(hours=1))
    session = make_session(user)

    result = await _authenticate_websocket(ws, session)

    assert result is None
    ws.close.assert_awaited_once_with(code=4401)


@pytest.mark.asyncio
async def test_valid_token_returns_user():
    token = create_access_token(USER_ID, TENANT_ID, "STAFF")
    ws = make_websocket(token)
    user = make_user()
    session = make_session(user)

    result = await _authenticate_websocket(ws, session)

    assert result is user
    ws.close.assert_not_awaited()
