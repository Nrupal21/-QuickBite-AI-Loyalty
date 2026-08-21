"""Unit tests for AuthService.request_contact_otp / verify_contact_otp — the
"Join Us" flow's second-contact-method verification (POST
/auth/register-restaurant/contact-otp/request and .../verify).

The step is contact-shaped, not phone-shaped: an email-registered user
verifies a phone here, a phone-registered user verifies an email, and the
service picks the channel from the value it was handed. Both directions are
covered below, since a bug that routes an email address to Twilio would pass
a phone-only suite.

DB session and Redis are mocked per AGENTS.md testing rules.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.encryption import sha256_hex
from app.db.models.user import User
from app.schemas.auth import ContactOtpRequestRequest, ContactOtpVerifyRequest
from app.services.auth_service import AuthService

PHONE = "+15550001234"
OTP_CODE = "482913"


def make_session() -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    return session


def make_user() -> User:
    user = User(
        tenant_id=None,
        role_id=uuid.uuid4(),
        email_hash=sha256_hex("owner@marcos.in"),
        encrypted_email="",
        hashed_password="$2b$12$fakehashfakehashfakehash",
        mfa_enabled=False,
        email_verified=True,
        is_active=True,
    )
    user.id = uuid.uuid4()
    return user


# --- request_contact_otp -------------------------------------------------


@pytest.mark.asyncio
async def test_request_contact_otp_sends_within_limits(mocker):
    user = make_user()
    session = make_session()
    mocker.patch(
        "app.services.auth_service.cache_service.exists", AsyncMock(return_value=False)
    )
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    mocker.patch("app.services.auth_service.generate_otp_code", return_value=OTP_CODE)
    send_sms = mocker.patch(
        "app.services.auth_service.messaging_service.send_business_phone_otp_sms",
        AsyncMock(return_value=True),
    )

    response = await AuthService(session=session).request_contact_otp(
        user, ContactOtpRequestRequest(contact=PHONE)
    )

    assert response.status == "otp_sent"
    send_sms.assert_awaited_once_with(PHONE, OTP_CODE)
    # Stores SHA-256(otp), never the plaintext code.
    stored_call = next(
        call for call in cache_set.await_args_list if call.args[0].startswith("contact_otp:")
    )
    assert stored_call.args[1] == sha256_hex(OTP_CODE)


@pytest.mark.asyncio
async def test_request_contact_otp_rate_limited_returns_429(mocker):
    user = make_user()
    session = make_session()
    mocker.patch(
        "app.services.auth_service.cache_service.exists", AsyncMock(return_value=True)
    )

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).request_contact_otp(
            user, ContactOtpRequestRequest(contact=PHONE)
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "OTP_RATE_LIMIT"


# --- verify_contact_otp ----------------------------------------------------


@pytest.mark.asyncio
async def test_verify_contact_otp_correct_code_issues_token(mocker):
    user = make_user()
    session = make_session()
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=sha256_hex(OTP_CODE)),
    )
    mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())
    mocker.patch(
        "app.services.auth_service.generate_password_reset_token",
        return_value="contact-verify-tok",
    )
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())

    response = await AuthService(session=session).verify_contact_otp(
        user, ContactOtpVerifyRequest(contact=PHONE, otp_code=OTP_CODE)
    )

    assert response.contact_verification_token == "contact-verify-tok"
    stored_key, stored_value = cache_set.await_args_list[0].args[0:2]
    assert stored_key == "contact_verify_token:contact-verify-tok"
    payload = json.loads(stored_value)
    assert payload["user_id"] == str(user.id)
    assert payload["contact_hash"] == sha256_hex(PHONE)
    assert payload["contact_type"] == "phone"


@pytest.mark.asyncio
async def test_verify_contact_otp_no_pending_code_returns_400(mocker):
    user = make_user()
    session = make_session()
    mocker.patch("app.services.auth_service.cache_service.get", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).verify_contact_otp(
            user, ContactOtpVerifyRequest(contact=PHONE, otp_code=OTP_CODE)
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "OTP_CODE_EXPIRED"


@pytest.mark.asyncio
async def test_verify_contact_otp_wrong_code_returns_401_with_attempts_remaining(mocker):
    user = make_user()
    session = make_session()
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=sha256_hex(OTP_CODE)),
    )
    mocker.patch("app.services.auth_service.cache_service.incr", AsyncMock(return_value=1))

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).verify_contact_otp(
            user, ContactOtpVerifyRequest(contact=PHONE, otp_code="000000")
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "OTP_CODE_INVALID"
    assert exc_info.value.detail["error"]["attempts_remaining"] == 2


@pytest.mark.asyncio
async def test_verify_contact_otp_too_many_attempts_clears_code(mocker):
    user = make_user()
    session = make_session()
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=sha256_hex(OTP_CODE)),
    )
    mocker.patch("app.services.auth_service.cache_service.incr", AsyncMock(return_value=3))
    cache_delete = mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).verify_contact_otp(
            user, ContactOtpVerifyRequest(contact=PHONE, otp_code="000000")
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "OTP_TOO_MANY_ATTEMPTS"
    assert cache_delete.await_count == 2  # otp key + attempts key


# --- the email direction of the same step --------------------------------


EMAIL = "owner@marcos.in"


@pytest.mark.asyncio
async def test_request_contact_otp_with_email_sends_email_not_sms(mocker):
    """A phone-registered user verifies an email here.

    The regression this guards: routing by anything other than the contact's
    own shape sends a business verification code to Twilio with an email
    address in the `to` field, which fails silently and strands the signup.
    """
    user = make_user()
    session = make_session()
    mocker.patch(
        "app.services.auth_service.cache_service.exists", AsyncMock(return_value=False)
    )
    mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    mocker.patch("app.services.auth_service.generate_otp_code", return_value=OTP_CODE)
    send_email = mocker.patch(
        "app.services.auth_service.messaging_service.send_business_email_otp",
        AsyncMock(return_value=True),
    )
    send_sms = mocker.patch(
        "app.services.auth_service.messaging_service.send_business_phone_otp_sms",
        AsyncMock(return_value=True),
    )

    response = await AuthService(session=session).request_contact_otp(
        user, ContactOtpRequestRequest(contact=EMAIL)
    )

    assert response.channel == "email"
    send_email.assert_awaited_once_with(EMAIL, OTP_CODE)
    send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_contact_otp_normalises_email_case(mocker):
    """Request and verify must hash the same string.

    Typing "Owner@Marcos.in" on one screen and "owner@marcos.in" on the next
    would otherwise store the code under one key and look for it under
    another — a correct code that silently never matches.
    """
    user = make_user()
    session = make_session()
    mocker.patch(
        "app.services.auth_service.cache_service.exists", AsyncMock(return_value=False)
    )
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    mocker.patch("app.services.auth_service.generate_otp_code", return_value=OTP_CODE)
    mocker.patch(
        "app.services.auth_service.messaging_service.send_business_email_otp",
        AsyncMock(return_value=True),
    )

    await AuthService(session=session).request_contact_otp(
        user, ContactOtpRequestRequest(contact="  Owner@Marcos.IN  ")
    )

    stored_call = next(
        call for call in cache_set.await_args_list if call.args[0].startswith("contact_otp:")
    )
    assert stored_call.args[0] == f"contact_otp:{user.id}:{sha256_hex(EMAIL)}"


@pytest.mark.asyncio
async def test_verify_contact_otp_with_email_records_email_type(mocker):
    user = make_user()
    session = make_session()
    mocker.patch(
        "app.services.auth_service.cache_service.get",
        AsyncMock(return_value=sha256_hex(OTP_CODE)),
    )
    mocker.patch("app.services.auth_service.cache_service.delete", AsyncMock())
    mocker.patch(
        "app.services.auth_service.generate_password_reset_token",
        return_value="contact-verify-tok",
    )
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())

    await AuthService(session=session).verify_contact_otp(
        user, ContactOtpVerifyRequest(contact=EMAIL, otp_code=OTP_CODE)
    )

    payload = json.loads(cache_set.await_args_list[0].args[1])
    assert payload["contact_type"] == "email"
    assert payload["contact_hash"] == sha256_hex(EMAIL)
