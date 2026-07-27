"""Unit tests for app.core.email_transport — SMTP send, MIME shape, failure modes.

aiosmtplib is mocked per AGENTS.md testing rules; no test opens a socket.
Every failure path must return False rather than raise: a dead mail server
must never fail a registration, a lockout, an invite, or an OTP request.
"""

import asyncio
from email.message import EmailMessage
from unittest.mock import AsyncMock

import pytest
from aiosmtplib import SMTPAuthenticationError

from app.core import email_transport
from app.core.config import settings

pytestmark = pytest.mark.allow_smtp  # this module patches aiosmtplib itself

SEND_TARGET = "app.core.email_transport.aiosmtplib.send"


@pytest.fixture
def smtp_configured(mocker):
    """Minimal working SMTP config, applied per-test so .env cannot influence results."""
    mocker.patch.object(settings, "EMAIL_ENABLED", True)
    mocker.patch.object(settings, "EMAIL_PROVIDER", "smtp")
    mocker.patch.object(settings, "SMTP_HOST", "smtp.gmail.com")
    mocker.patch.object(settings, "SMTP_USERNAME", "bot@quickbite.ai")
    mocker.patch.object(settings, "SMTP_PASSWORD", "apppassword")
    mocker.patch.object(settings, "SMTP_SECURITY", "starttls")
    mocker.patch.object(settings, "EMAIL_FROM_NAME", "QuickBite AI")
    mocker.patch.object(settings, "EMAIL_FROM_ADDRESS", "no-reply@quickbite.ai")


async def send(**overrides) -> bool:
    kwargs = {
        "to_email": "owner@marcos.in",
        "subject": "Test subject",
        "html_body": "<p>hello</p>",
        "text_body": "hello",
        "log_event": "email.test",
    }
    kwargs.update(overrides)
    return await email_transport.send_email(**kwargs)


@pytest.mark.asyncio
async def test_successful_send_returns_true(mocker, smtp_configured):
    send_mock = mocker.patch(SEND_TARGET, AsyncMock())

    assert await send() is True
    send_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_smtp_error_returns_false_without_raising(mocker, smtp_configured):
    mocker.patch(SEND_TARGET, AsyncMock(side_effect=SMTPAuthenticationError(535, "bad password")))

    assert await send() is False


@pytest.mark.asyncio
async def test_timeout_returns_false(mocker, smtp_configured):
    """A hung Gmail connection must not hold the caller open indefinitely."""
    mocker.patch(SEND_TARGET, AsyncMock(side_effect=asyncio.TimeoutError()))

    assert await send() is False


@pytest.mark.asyncio
async def test_missing_credentials_never_opens_a_connection(mocker, smtp_configured):
    mocker.patch.object(settings, "SMTP_PASSWORD", "")
    send_mock = mocker.patch(SEND_TARGET, AsyncMock())

    assert await send() is False
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_disabled_never_opens_a_connection(mocker, smtp_configured):
    mocker.patch.object(settings, "EMAIL_ENABLED", False)
    send_mock = mocker.patch(SEND_TARGET, AsyncMock())

    assert await send() is False
    send_mock.assert_not_awaited()


# --- MIME shape ---------------------------------------------------------


@pytest.mark.asyncio
async def test_message_is_multipart_alternative_with_plaintext_first(mocker, smtp_configured):
    """RFC 2046: the richest alternative goes last, so text must precede HTML.

    HTML-only mail is a strong spam signal and is unreadable in text clients
    and screen readers.
    """
    send_mock = mocker.patch(SEND_TARGET, AsyncMock())

    await send(html_body="<p>rich</p>", text_body="plain")

    message: EmailMessage = send_mock.await_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    subtypes = [part.get_content_subtype() for part in message.iter_parts()]
    assert subtypes == ["plain", "html"]
    assert "plain" in message.get_body(preferencelist=("plain",)).get_content()
    assert "rich" in message.get_body(preferencelist=("html",)).get_content()


@pytest.mark.asyncio
async def test_from_header_carries_display_name_and_address(mocker, smtp_configured):
    send_mock = mocker.patch(SEND_TARGET, AsyncMock())

    await send()

    message: EmailMessage = send_mock.await_args.args[0]
    assert message["From"] == "QuickBite AI <no-reply@quickbite.ai>"
    assert message["To"] == "owner@marcos.in"
    assert message["Subject"] == "Test subject"
    assert message["Message-ID"]


@pytest.mark.asyncio
async def test_from_address_falls_back_to_smtp_username(mocker, smtp_configured):
    """Gmail rewrites From to the authenticated account, so an empty override
    should resolve to that account rather than to an empty header."""
    mocker.patch.object(settings, "EMAIL_FROM_ADDRESS", "")
    send_mock = mocker.patch(SEND_TARGET, AsyncMock())

    await send()

    assert "bot@quickbite.ai" in send_mock.await_args.args[0]["From"]


# --- provider switch ----------------------------------------------------


@pytest.mark.asyncio
async def test_sendgrid_provider_does_not_use_smtp(mocker, smtp_configured):
    mocker.patch.object(settings, "EMAIL_PROVIDER", "sendgrid")
    mocker.patch.object(settings, "SENDGRID_API_KEY", "")
    smtp_mock = mocker.patch(SEND_TARGET, AsyncMock())

    assert await send() is False  # no API key configured
    smtp_mock.assert_not_awaited()


# --- TLS flag derivation ------------------------------------------------


@pytest.mark.parametrize(
    ("security", "expected"),
    [
        ("starttls", {"use_tls": False, "start_tls": True}),
        ("tls", {"use_tls": True, "start_tls": False}),
        ("none", {"use_tls": False, "start_tls": False}),
    ],
)
def test_smtp_tls_kwargs_never_sets_both_flags(mocker, security, expected):
    """aiosmtplib raises ValueError when both are True — which the transport's
    best-effort handler would swallow into a silent total outage."""
    mocker.patch.object(settings, "SMTP_SECURITY", security)

    kwargs = settings.smtp_tls_kwargs

    assert kwargs == expected
    assert not (kwargs["use_tls"] and kwargs["start_tls"])


@pytest.mark.asyncio
async def test_tls_kwargs_are_passed_through_to_aiosmtplib(mocker, smtp_configured):
    mocker.patch.object(settings, "SMTP_SECURITY", "tls")
    mocker.patch.object(settings, "SMTP_PORT", 465)
    send_mock = mocker.patch(SEND_TARGET, AsyncMock())

    await send()

    kwargs = send_mock.await_args.kwargs
    assert kwargs["use_tls"] is True
    assert kwargs["start_tls"] is False
    assert kwargs["port"] == 465
    assert kwargs["hostname"] == "smtp.gmail.com"
