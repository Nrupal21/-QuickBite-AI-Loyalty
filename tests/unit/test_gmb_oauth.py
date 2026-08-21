"""Unit tests for REVIEW-03 — GMB OAuth connect/refresh/disconnect.

httpx and Redis (cache_service) are always mocked (AGENTS.md §7): these
tests never talk to Google or boot a real Redis instance.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.encryption import decrypt_pii, encrypt_pii
from app.db.models.reputation import GMBProfile
from app.services import gmb_service

TENANT_ID = uuid.uuid4()
BRANCH_ID = uuid.uuid4()


def make_profile(**overrides) -> GMBProfile:
    defaults = {
        "tenant_id": TENANT_ID,
        "branch_id": BRANCH_ID,
        "gmb_account_id": "acct-1",
        "gmb_location_id": "loc-1",
        "encrypted_access_token": encrypt_pii("old-access-token"),
        "encrypted_refresh_token": encrypt_pii("refresh-token-1"),
        "token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "token_rotated_at": None,
        "gmb_sync_cursor": None,
        "last_synced_at": None,
        "is_connected": True,
    }
    defaults.update(overrides)
    return GMBProfile(**defaults)


def make_session(execute_results: list | None = None) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    if execute_results is not None:
        results = []
        for value in execute_results:
            result = MagicMock()
            result.scalar_one_or_none.return_value = value
            results.append(result)
        session.execute = AsyncMock(side_effect=results)
    return session


def mock_httpx_client(mocker, *, post=None, get=None) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if post is not None:
        client.post = post
    if get is not None:
        client.get = get
    mocker.patch("app.services.gmb_service.httpx.AsyncClient", MagicMock(return_value=client))
    return client


def json_response(status_code: int, payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = json.dumps(payload)
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=response
        )
    return response


# --- build_authorize_url -----------------------------------------------------


def test_authorize_url_carries_state_and_offline_access(mocker):
    mocker.patch.object(gmb_service.settings, "GOOGLE_CLIENT_ID", "client-123")
    mocker.patch.object(gmb_service.settings, "GMB_OAUTH_REDIRECT_URI", "https://app.quickbite.ai/gmb/callback")

    url = gmb_service.build_authorize_url("state-abc")

    assert "client_id=client-123" in url
    assert "state=state-abc" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "response_type=code" in url


# --- OAuth state (CSRF) -------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_state_round_trips_tenant_and_branch(mocker):
    store: dict[str, str] = {}
    mocker.patch(
        "app.services.gmb_service.cache_service.set",
        AsyncMock(side_effect=lambda key, value, ttl=None: store.__setitem__(key, value)),
    )
    mocker.patch(
        "app.services.gmb_service.cache_service.get", AsyncMock(side_effect=lambda key: store.get(key))
    )
    mocker.patch(
        "app.services.gmb_service.cache_service.delete", AsyncMock(side_effect=lambda key: store.pop(key, None))
    )

    state = await gmb_service.create_oauth_state(TENANT_ID, BRANCH_ID)
    parsed = await gmb_service.consume_oauth_state(state)

    assert parsed == {"tenant_id": str(TENANT_ID), "branch_id": str(BRANCH_ID)}


@pytest.mark.asyncio
async def test_oauth_state_is_single_use(mocker):
    store = {"gmb_oauth_state:used": json.dumps({"tenant_id": str(TENANT_ID), "branch_id": str(BRANCH_ID)})}
    mocker.patch(
        "app.services.gmb_service.cache_service.get", AsyncMock(side_effect=lambda key: store.get(key))
    )
    mocker.patch(
        "app.services.gmb_service.cache_service.delete", AsyncMock(side_effect=lambda key: store.pop(key, None))
    )

    first = await gmb_service.consume_oauth_state("used")
    second = await gmb_service.consume_oauth_state("used")

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_unknown_oauth_state_returns_none(mocker):
    mocker.patch("app.services.gmb_service.cache_service.get", AsyncMock(return_value=None))

    assert await gmb_service.consume_oauth_state("never-existed") is None


# --- token refresh -------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_valid_access_token_reuses_an_unexpired_token(mocker):
    profile = make_profile(token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30))
    session = make_session()
    post = AsyncMock()
    mock_httpx_client(mocker, post=post)

    token = await gmb_service.get_valid_access_token(session, profile)

    assert token == "old-access-token"
    post.assert_not_called()


@pytest.mark.asyncio
async def test_get_valid_access_token_refreshes_and_persists_when_stale(mocker):
    profile = make_profile(token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    session = make_session()
    post = AsyncMock(return_value=json_response(200, {"access_token": "new-access-token", "expires_in": 3600}))
    mock_httpx_client(mocker, post=post)

    token = await gmb_service.get_valid_access_token(session, profile)

    assert token == "new-access-token"
    assert decrypt_pii(profile.encrypted_access_token) == "new-access-token"
    assert profile.token_rotated_at is not None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_with_revoked_grant_disconnects_the_profile(mocker):
    profile = make_profile(token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    session = make_session()
    post = AsyncMock(return_value=json_response(400, {"error": "invalid_grant"}))
    mock_httpx_client(mocker, post=post)

    with pytest.raises(gmb_service.GMBTokenExpired):
        await gmb_service.get_valid_access_token(session, profile)

    assert profile.is_connected is False
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_valid_access_token_raises_not_connected(mocker):
    profile = make_profile(is_connected=False)
    session = make_session()

    with pytest.raises(gmb_service.GMBNotConnected):
        await gmb_service.get_valid_access_token(session, profile)


# --- connect (OAuth callback) --------------------------------------------------


@pytest.mark.asyncio
async def test_complete_connect_creates_a_new_profile(mocker):
    token_response = json_response(
        200, {"access_token": "fresh-access", "refresh_token": "fresh-refresh", "expires_in": 3600}
    )
    accounts_response = json_response(200, {"accounts": [{"name": "accounts/999"}]})
    locations_response = json_response(200, {"locations": [{"name": "locations/111"}]})

    post = AsyncMock(return_value=token_response)
    get = AsyncMock(side_effect=[accounts_response, locations_response])
    mock_httpx_client(mocker, post=post, get=get)

    session = make_session([None])  # no existing profile

    profile = await gmb_service.complete_connect(
        session, code="auth-code", tenant_id=TENANT_ID, branch_id=BRANCH_ID
    )

    assert profile.gmb_account_id == "999"
    assert profile.gmb_location_id == "111"
    assert decrypt_pii(profile.encrypted_access_token) == "fresh-access"
    assert decrypt_pii(profile.encrypted_refresh_token) == "fresh-refresh"
    assert profile.is_connected is True
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_connect_wraps_a_403_from_the_accounts_api(mocker):
    """Regression: a live test hit this exact shape — Google's Business
    Profile Account Management API 403ing (no GMB access on this token/
    Cloud project) must surface as a clean GMBOAuthError -> 400, not an
    unhandled httpx.HTTPStatusError -> 500 mid-redirect."""
    token_response = json_response(
        200, {"access_token": "fresh-access", "refresh_token": "fresh-refresh", "expires_in": 3600}
    )
    forbidden_accounts_response = json_response(403, {"error": {"message": "forbidden"}})
    post = AsyncMock(return_value=token_response)
    get = AsyncMock(return_value=forbidden_accounts_response)
    mock_httpx_client(mocker, post=post, get=get)
    session = make_session()

    with pytest.raises(gmb_service.GMBOAuthError):
        await gmb_service.complete_connect(session, code="auth-code", tenant_id=TENANT_ID, branch_id=BRANCH_ID)


@pytest.mark.asyncio
async def test_exchange_code_wraps_a_connection_failure(mocker):
    post = AsyncMock(side_effect=httpx.ConnectError("boom"))
    mock_httpx_client(mocker, post=post)

    with pytest.raises(gmb_service.GMBOAuthError):
        await gmb_service.exchange_code_for_tokens("auth-code")


@pytest.mark.asyncio
async def test_refresh_access_token_wraps_a_connection_failure(mocker):
    post = AsyncMock(side_effect=httpx.ConnectError("boom"))
    mock_httpx_client(mocker, post=post)

    with pytest.raises(gmb_service.GMBOAuthError):
        await gmb_service.refresh_access_token("refresh-token-1")


@pytest.mark.asyncio
async def test_complete_connect_without_refresh_token_raises(mocker):
    token_response = json_response(200, {"access_token": "fresh-access", "expires_in": 3600})
    post = AsyncMock(return_value=token_response)
    mock_httpx_client(mocker, post=post)
    session = make_session()

    with pytest.raises(gmb_service.GMBOAuthError):
        await gmb_service.complete_connect(session, code="auth-code", tenant_id=TENANT_ID, branch_id=BRANCH_ID)


@pytest.mark.asyncio
async def test_complete_connect_updates_an_existing_profile(mocker):
    existing = make_profile(gmb_account_id="old-acct", gmb_location_id="old-loc")
    token_response = json_response(
        200, {"access_token": "fresh-access", "refresh_token": "fresh-refresh", "expires_in": 3600}
    )
    accounts_response = json_response(200, {"accounts": [{"name": "accounts/999"}]})
    locations_response = json_response(200, {"locations": [{"name": "locations/111"}]})
    post = AsyncMock(return_value=token_response)
    get = AsyncMock(side_effect=[accounts_response, locations_response])
    mock_httpx_client(mocker, post=post, get=get)
    session = make_session([existing])

    profile = await gmb_service.complete_connect(
        session, code="auth-code", tenant_id=TENANT_ID, branch_id=BRANCH_ID
    )

    assert profile is existing
    assert profile.gmb_account_id == "999"
    session.add.assert_not_called()


# --- disconnect -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_wipes_tokens_and_revokes_at_google(mocker):
    profile = make_profile()
    session = make_session([profile])
    post = AsyncMock(return_value=json_response(200, {}))
    mock_httpx_client(mocker, post=post)

    result = await gmb_service.disconnect(session, TENANT_ID, BRANCH_ID)

    assert result is profile
    assert profile.is_connected is False
    assert profile.encrypted_access_token == ""
    assert profile.encrypted_refresh_token == ""
    post.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_survives_google_being_unreachable(mocker):
    profile = make_profile()
    session = make_session([profile])
    post = AsyncMock(side_effect=httpx.ConnectError("boom"))
    mock_httpx_client(mocker, post=post)

    result = await gmb_service.disconnect(session, TENANT_ID, BRANCH_ID)

    assert result.is_connected is False


@pytest.mark.asyncio
async def test_disconnect_missing_profile_returns_none(mocker):
    session = make_session([None])

    result = await gmb_service.disconnect(session, TENANT_ID, BRANCH_ID)

    assert result is None


@pytest.mark.asyncio
async def test_disconnect_already_disconnected_is_a_no_op(mocker):
    profile = make_profile(is_connected=False)
    session = make_session([profile])
    post = AsyncMock()
    mock_httpx_client(mocker, post=post)

    result = await gmb_service.disconnect(session, TENANT_ID, BRANCH_ID)

    assert result is profile
    post.assert_not_called()
    session.commit.assert_not_called()
