"""Unit tests for the Meta WhatsApp Business Platform (Cloud API) client.

httpx is always mocked (AGENTS.md §7) — these tests never talk to Meta.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services import whatsapp_service


def mock_httpx_client(mocker, *, post=None, get=None) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if post is not None:
        client.post = post
    if get is not None:
        client.get = get
    mocker.patch("app.services.whatsapp_service.httpx.AsyncClient", MagicMock(return_value=client))
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


# --- token exchange -------------------------------------------------------


@pytest.mark.asyncio
async def test_exchange_code_for_token_returns_the_payload(mocker):
    get = AsyncMock(return_value=json_response(200, {"access_token": "short-lived"}))
    mock_httpx_client(mocker, get=get)

    result = await whatsapp_service.exchange_code_for_token("the-code")

    assert result["access_token"] == "short-lived"


@pytest.mark.asyncio
async def test_exchange_code_for_token_wraps_a_non_200(mocker):
    get = AsyncMock(return_value=json_response(400, {"error": "invalid code"}))
    mock_httpx_client(mocker, get=get)

    with pytest.raises(whatsapp_service.WhatsAppOAuthError):
        await whatsapp_service.exchange_code_for_token("bad-code")


@pytest.mark.asyncio
async def test_exchange_code_for_token_wraps_a_connection_failure(mocker):
    get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    mock_httpx_client(mocker, get=get)

    with pytest.raises(whatsapp_service.WhatsAppOAuthError):
        await whatsapp_service.exchange_code_for_token("the-code")


@pytest.mark.asyncio
async def test_exchange_for_long_lived_token_returns_the_payload(mocker):
    get = AsyncMock(return_value=json_response(200, {"access_token": "long-lived", "expires_in": 5184000}))
    mock_httpx_client(mocker, get=get)

    result = await whatsapp_service.exchange_for_long_lived_token("short-lived")

    assert result["access_token"] == "long-lived"


# --- webhook subscribe ------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_app_to_waba_succeeds_silently(mocker):
    post = AsyncMock(return_value=json_response(200, {"success": True}))
    mock_httpx_client(mocker, post=post)

    await whatsapp_service.subscribe_app_to_waba("waba-1", "token")  # must not raise
    post.assert_awaited_once()


@pytest.mark.asyncio
async def test_subscribe_app_to_waba_raises_on_failure(mocker):
    post = AsyncMock(return_value=json_response(400, {"error": "bad token"}))
    mock_httpx_client(mocker, post=post)

    with pytest.raises(whatsapp_service.WhatsAppOAuthError):
        await whatsapp_service.subscribe_app_to_waba("waba-1", "token")


# --- templates --------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_templates_follows_pagination(mocker):
    page1 = json_response(
        200,
        {
            "data": [{"id": "1", "name": "promo_a"}],
            "paging": {"next": "https://graph.facebook.com/v21.0/waba-1/message_templates?after=x"},
        },
    )
    page2 = json_response(200, {"data": [{"id": "2", "name": "promo_b"}]})
    get = AsyncMock(side_effect=[page1, page2])
    mock_httpx_client(mocker, get=get)

    templates = await whatsapp_service.fetch_templates("waba-1", "token")

    assert [t["name"] for t in templates] == ["promo_a", "promo_b"]
    assert get.await_count == 2


# --- send_template_message ---------------------------------------------------


@pytest.mark.asyncio
async def test_send_template_message_returns_the_wamid(mocker):
    post = AsyncMock(return_value=json_response(200, {"messages": [{"id": "wamid.abc123"}]}))
    mock_httpx_client(mocker, post=post)

    wamid = await whatsapp_service.send_template_message(
        "phone-1", "token", "+919876543210", "reward_unlocked", "en"
    )

    assert wamid == "wamid.abc123"
    payload = post.await_args.kwargs["json"]
    assert payload["to"] == "+919876543210"
    assert payload["template"]["name"] == "reward_unlocked"


@pytest.mark.asyncio
async def test_send_template_message_raises_with_metas_error_code(mocker):
    post = AsyncMock(
        return_value=json_response(400, {"error": {"code": 131056, "message": "rate limited"}})
    )
    mock_httpx_client(mocker, post=post)

    with pytest.raises(whatsapp_service.WhatsAppSendError) as exc_info:
        await whatsapp_service.send_template_message(
            "phone-1", "token", "+919876543210", "reward_unlocked", "en"
        )

    assert exc_info.value.error_code == "131056"


@pytest.mark.asyncio
async def test_send_template_message_wraps_a_connection_failure(mocker):
    post = AsyncMock(side_effect=httpx.ConnectError("boom"))
    mock_httpx_client(mocker, post=post)

    with pytest.raises(whatsapp_service.WhatsAppSendError):
        await whatsapp_service.send_template_message(
            "phone-1", "token", "+919876543210", "reward_unlocked", "en"
        )


# --- webhook verification handshake ------------------------------------------


def test_verify_webhook_challenge_matches(mocker):
    mocker.patch.object(whatsapp_service.settings, "META_WEBHOOK_VERIFY_TOKEN", "the-token")

    assert (
        whatsapp_service.verify_webhook_challenge("subscribe", "the-token", "echo-me") == "echo-me"
    )


def test_verify_webhook_challenge_rejects_wrong_token(mocker):
    mocker.patch.object(whatsapp_service.settings, "META_WEBHOOK_VERIFY_TOKEN", "the-token")

    assert whatsapp_service.verify_webhook_challenge("subscribe", "wrong-token", "echo-me") is None


def test_verify_webhook_challenge_rejects_wrong_mode(mocker):
    mocker.patch.object(whatsapp_service.settings, "META_WEBHOOK_VERIFY_TOKEN", "the-token")

    assert whatsapp_service.verify_webhook_challenge("unsubscribe", "the-token", "echo-me") is None
