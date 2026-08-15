"""Unit tests for REVIEW-02's GMB reply-posting client.

Scope is posting only (see gmb_service.py's module docstring) — OAuth
connect/refresh is REVIEW-03. httpx is always mocked (AGENTS.md §7: external
services are never called for real in a unit test).
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.encryption import encrypt_pii
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
        "encrypted_access_token": encrypt_pii("real-access-token"),
        "encrypted_refresh_token": encrypt_pii("real-refresh-token"),
        "token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "is_connected": True,
    }
    defaults.update(overrides)
    return GMBProfile(**defaults)


def mock_client(mocker, response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.put = AsyncMock(return_value=response)
    mocker.patch("app.services.gmb_service.httpx.AsyncClient", MagicMock(return_value=client))
    return client


@pytest.mark.asyncio
async def test_posts_the_reply_with_the_decrypted_bearer_token(mocker):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    client = mock_client(mocker, response)

    await gmb_service.post_review_reply(make_profile(), "gmb-review-1", "Thanks so much!")

    _, kwargs = client.put.await_args
    assert kwargs["headers"]["Authorization"] == "Bearer real-access-token"
    assert kwargs["json"] == {"comment": "Thanks so much!"}
    url = client.put.await_args.args[0]
    assert "acct-1" in url
    assert "loc-1" in url
    assert "gmb-review-1" in url


@pytest.mark.asyncio
async def test_not_connected_raises_without_an_http_call(mocker):
    client = mock_client(mocker, MagicMock())

    with pytest.raises(gmb_service.GMBNotConnected):
        await gmb_service.post_review_reply(make_profile(is_connected=False), "gmb-review-1", "Thanks!")

    client.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_token_raises_without_an_http_call(mocker):
    client = mock_client(mocker, MagicMock())
    expired = make_profile(token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    with pytest.raises(gmb_service.GMBTokenExpired):
        await gmb_service.post_review_reply(expired, "gmb-review-1", "Thanks!")

    client.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_error_propagates(mocker):
    import httpx

    response = MagicMock()
    response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("boom", request=MagicMock(), response=MagicMock())
    )
    mock_client(mocker, response)

    with pytest.raises(httpx.HTTPStatusError):
        await gmb_service.post_review_reply(make_profile(), "gmb-review-1", "Thanks!")
