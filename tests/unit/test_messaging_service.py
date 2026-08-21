"""Unit tests for the messaging_service façades.

These assert the wiring the four callers depend on: the right template, the
right log-event prefix, and the transport's boolean passed through unchanged.
The log prefixes are load-bearing — they are the only signal an operator has
that email delivery has degraded, and they were preserved verbatim across the
SendGrid-to-SMTP move.

Also closes the send_otp_email coverage gap: before this file it was the only
sender with no test at all.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.config import settings
from app.services import messaging_service

TRANSPORT = "app.services.messaging_service.email_transport.send_email"


@pytest.fixture
def transport(mocker):
    return mocker.patch(TRANSPORT, AsyncMock(return_value=True))


@pytest.mark.asyncio
async def test_verification_email_uses_its_template_and_log_event(transport):
    assert await messaging_service.send_verification_email(
        "owner@marcos.in", "https://q.ai/verify?token=t"
    ) is True

    kwargs = transport.await_args.kwargs
    assert kwargs["log_event"] == "email.verification"
    assert kwargs["to_email"] == "owner@marcos.in"
    assert "https://q.ai/verify?token=t" in kwargs["html_body"]
    assert "https://q.ai/verify?token=t" in kwargs["text_body"]


@pytest.mark.asyncio
async def test_account_locked_email_formats_the_unlock_time(transport):
    unlock_at = datetime(2026, 7, 27, 14, 30, tzinfo=timezone.utc)

    await messaging_service.send_account_locked_email("owner@marcos.in", unlock_at)

    kwargs = transport.await_args.kwargs
    assert kwargs["log_event"] == "email.account_locked"
    # Formatting lives in the façade, so both parts show the identical string.
    assert "14:30 UTC" in kwargs["html_body"]
    assert "14:30 UTC" in kwargs["text_body"]


@pytest.mark.asyncio
async def test_staff_invite_email_humanises_the_role_name(transport):
    await messaging_service.send_staff_invite_email(
        "chef@marcos.in", "https://q.ai/accept?token=t", "SUPER_ADMIN"
    )

    kwargs = transport.await_args.kwargs
    assert kwargs["log_event"] == "email.staff_invite"
    assert "Super Admin" in kwargs["html_body"]
    assert "SUPER_ADMIN" not in kwargs["html_body"]


@pytest.mark.asyncio
async def test_otp_email_carries_the_code(transport):
    """The one sender that had no test before this file existed."""
    assert await messaging_service.send_otp_email("customer@example.com", "482913") is True

    kwargs = transport.await_args.kwargs
    assert kwargs["log_event"] == "email.otp"
    assert "482913" in kwargs["html_body"]
    assert "482913" in kwargs["text_body"]


@pytest.mark.asyncio
async def test_senders_pass_through_a_transport_failure(mocker):
    """Best-effort contract: callers audit-log this boolean, so it must be honest."""
    mocker.patch(TRANSPORT, AsyncMock(return_value=False))

    assert await messaging_service.send_otp_email("customer@example.com", "482913") is False
    assert await messaging_service.send_verification_email("o@m.in", "https://q.ai/v") is False


# --- SMS ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_otp_sms_skipped_without_credentials(mocker):
    # SMS_PROVIDER is pinned here rather than left to whatever the local
    # .env happens to have — a dev box with SMS_PROVIDER=2factor must not
    # flip which provider this test is actually exercising.
    mocker.patch.object(settings, "SMS_PROVIDER", "twilio")
    mocker.patch.object(settings, "TWILIO_ACCOUNT_SID", "")

    assert await messaging_service.send_otp_sms("+919876543210", "482913") is False


@pytest.mark.asyncio
async def test_otp_sms_runs_the_blocking_client_off_the_event_loop(mocker):
    """The Twilio SDK is synchronous; calling it inline would stall every
    request on the worker for a full HTTPS round trip."""
    mocker.patch.object(settings, "SMS_PROVIDER", "twilio")
    mocker.patch.object(settings, "TWILIO_ACCOUNT_SID", "sid")
    mocker.patch.object(settings, "TWILIO_AUTH_TOKEN", "token")
    mocker.patch("app.services.messaging_service.TwilioClient")
    to_thread = mocker.patch(
        "app.services.messaging_service.asyncio.to_thread", AsyncMock(return_value=None)
    )

    assert await messaging_service.send_otp_sms("+919876543210", "482913") is True
    to_thread.assert_awaited_once()


# --- 2Factor.in (SMS_PROVIDER=2factor) -----------------------------------


def mock_get_client(mocker, response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response)
    mocker.patch(
        "app.services.messaging_service.httpx.AsyncClient", MagicMock(return_value=client)
    )
    return client


@pytest.mark.asyncio
async def test_2factor_otp_skipped_without_credentials(mocker):
    mocker.patch.object(settings, "TWOFACTOR_API_KEY", "")

    assert (
        await messaging_service.send_otp_sms_via_2factor(
            "+919876543210", "482913", template="customer_otp_sms"
        )
        is False
    )


@pytest.mark.asyncio
async def test_2factor_otp_sends_the_apps_own_otp_to_the_bare_number(mocker):
    """The '+' the app stores every phone number with must not reach
    2Factor's URL path, and the OTP in the URL must be the one this app
    generated (never a value 2Factor invents server-side)."""
    mocker.patch.object(settings, "TWOFACTOR_API_KEY", "the-key")
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"Status": "Success", "Details": "session-id"})
    client = mock_get_client(mocker, response)

    assert (
        await messaging_service.send_otp_sms_via_2factor(
            "+919876543210", "482913", template="customer_otp_sms"
        )
        is True
    )

    url = client.get.await_args.args[0]
    assert url == "https://2factor.in/API/V1/the-key/SMS/919876543210/482913"


@pytest.mark.asyncio
async def test_2factor_otp_reports_a_provider_error_status(mocker):
    mocker.patch.object(settings, "TWOFACTOR_API_KEY", "the-key")
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"Status": "Error", "Details": "Invalid API Key"})
    mock_get_client(mocker, response)

    assert (
        await messaging_service.send_otp_sms_via_2factor(
            "+919876543210", "482913", template="customer_otp_sms"
        )
        is False
    )


@pytest.mark.asyncio
async def test_2factor_otp_handles_a_transport_failure(mocker):
    mocker.patch.object(settings, "TWOFACTOR_API_KEY", "the-key")
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=httpx.ConnectTimeout("boom"))
    mocker.patch(
        "app.services.messaging_service.httpx.AsyncClient", MagicMock(return_value=client)
    )

    assert (
        await messaging_service.send_otp_sms_via_2factor(
            "+919876543210", "482913", template="customer_otp_sms"
        )
        is False
    )


@pytest.mark.asyncio
async def test_send_otp_sms_routes_to_2factor_when_configured(mocker):
    mocker.patch.object(settings, "SMS_PROVIDER", "2factor")
    via_2factor = mocker.patch(
        "app.services.messaging_service.send_otp_sms_via_2factor", AsyncMock(return_value=True)
    )
    via_twilio = mocker.patch(
        "app.services.messaging_service.notify", AsyncMock(return_value=True)
    )

    assert await messaging_service.send_otp_sms("+919876543210", "482913") is True

    via_2factor.assert_awaited_once_with(
        "+919876543210", "482913", template="customer_otp_sms"
    )
    via_twilio.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_otp_sms_defaults_to_twilio_provider(mocker):
    """Twilio is the code's default when SMS_PROVIDER isn't "2factor" — not
    an assertion about what the local .env happens to have set."""
    mocker.patch.object(settings, "SMS_PROVIDER", "twilio")
    via_2factor = mocker.patch(
        "app.services.messaging_service.send_otp_sms_via_2factor", AsyncMock(return_value=True)
    )
    via_twilio = mocker.patch(
        "app.services.messaging_service.notify", AsyncMock(return_value=True)
    )

    assert await messaging_service.send_otp_sms("+919876543210", "482913") is True

    via_twilio.assert_awaited_once()
    via_2factor.assert_not_awaited()
