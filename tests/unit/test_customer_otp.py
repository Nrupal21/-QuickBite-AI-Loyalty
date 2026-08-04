"""Unit tests for customer_otp_service — one per NEW-OTP-01/02 acceptance criterion.

DB session and Redis are mocked per AGENTS.md testing rules.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.encryption import encrypt_pii, sha256_hex
from app.db.models.customer import Customer
from app.schemas.customer_auth import OTPNewUserResponse, OTPRequest, OTPSentResponse, OTPVerify
from app.services import customer_otp_service

TENANT_ID = uuid.uuid4()
PHONE = "+919876543210"
EMAIL = "customer@example.com"
OTP_CODE = "482913"


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


def make_customer(**overrides) -> Customer:
    defaults = {
        "tenant_id": TENANT_ID,
        "phone_hash": sha256_hex(PHONE),
        "encrypted_phone": encrypt_pii(PHONE),
        "email_hash": None,
        "encrypted_email": None,
        "otp_attempts": 0,
        "total_stamps_alltime": 3,
        "current_reward_count": 3,
        "last_seen_at": None,
    }
    defaults.update(overrides)
    customer = Customer(**defaults)
    customer.id = uuid.uuid4()
    return customer


# --- NEW-OTP-01: otp-request -----------------------------------------------


@pytest.mark.asyncio
async def test_request_otp_registered_phone_sends_within_limits(mocker):
    customer = make_customer(otp_attempts=2)
    session = make_session([customer])
    mocker.patch("app.services.customer_otp_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.services.customer_otp_service.cache_service.incr", AsyncMock(return_value=1))
    cache_set = mocker.patch("app.services.customer_otp_service.cache_service.set", AsyncMock())
    send_sms = mocker.patch(
        "app.services.customer_otp_service.messaging_service.send_otp_sms", AsyncMock(return_value=True)
    )

    response = await customer_otp_service.request_otp(
        OTPRequest(identifier=PHONE, tenant_id=TENANT_ID), session
    )

    assert isinstance(response, OTPSentResponse)
    assert response.status == "sent"
    assert customer.otp_attempts == 0  # reset for a fresh code
    send_sms.assert_awaited_once()
    otp_calls = [c for c in cache_set.await_args_list if c.args[0].startswith("otp:")]
    assert len(otp_calls) == 1
    assert otp_calls[0].kwargs["ttl"] == 300


@pytest.mark.asyncio
async def test_request_otp_unregistered_identifier_returns_new_user_not_404(mocker):
    session = make_session([None])
    mocker.patch("app.services.customer_otp_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.services.customer_otp_service.cache_service.incr", AsyncMock(return_value=1))
    cache_set = mocker.patch("app.services.customer_otp_service.cache_service.set", AsyncMock())

    response = await customer_otp_service.request_otp(
        OTPRequest(identifier=PHONE, tenant_id=TENANT_ID), session
    )

    assert isinstance(response, OTPNewUserResponse)
    assert response.status == "new_user"
    assert response.registration_token
    reg_calls = [c for c in cache_set.await_args_list if c.args[0].startswith("pending_customer_reg:")]
    assert len(reg_calls) == 1
    stored = json.loads(reg_calls[0].args[1])
    assert stored["identifier"] == PHONE
    assert stored["identifier_type"] == "phone"


@pytest.mark.asyncio
async def test_request_otp_within_cooldown_returns_429(mocker):
    session = make_session([])
    mocker.patch("app.services.customer_otp_service.cache_service.exists", AsyncMock(return_value=True))

    with pytest.raises(HTTPException) as exc_info:
        await customer_otp_service.request_otp(
            OTPRequest(identifier=PHONE, tenant_id=TENANT_ID), session
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "OTP_RATE_LIMIT"
    assert exc_info.value.headers["Retry-After"] == "120"
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_otp_daily_limit_returns_429(mocker):
    session = make_session([])
    mocker.patch("app.services.customer_otp_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.services.customer_otp_service.cache_service.incr", AsyncMock(return_value=6))

    with pytest.raises(HTTPException) as exc_info:
        await customer_otp_service.request_otp(
            OTPRequest(identifier=PHONE, tenant_id=TENANT_ID), session
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "OTP_DAILY_LIMIT"


# --- NEW-OTP-02: otp-verify -------------------------------------------------


@pytest.mark.asyncio
async def test_verify_otp_correct_code_issues_token_and_updates_last_seen(mocker):
    customer = make_customer(otp_attempts=1)
    session = make_session([customer])
    mocker.patch(
        "app.services.customer_otp_service.cache_service.get",
        AsyncMock(return_value=sha256_hex(OTP_CODE)),
    )
    mocker.patch("app.services.customer_otp_service.cache_service.delete", AsyncMock())
    mocker.patch(
        "app.services.customer_otp_service.create_customer_token", return_value="customer-jwt-abc"
    )

    response, token = await customer_otp_service.verify_otp(
        OTPVerify(identifier=PHONE, tenant_id=TENANT_ID, otp_code=OTP_CODE), session
    )

    assert response.status == "verified"
    assert response.customer_id == str(customer.id)
    assert response.total_stamps_alltime == 3
    assert token == "customer-jwt-abc"
    assert customer.otp_attempts == 0
    assert customer.last_seen_at is not None


@pytest.mark.asyncio
async def test_verify_otp_wrong_code_returns_401_with_attempts_remaining(mocker):
    customer = make_customer(otp_attempts=0)
    session = make_session([customer])
    mocker.patch(
        "app.services.customer_otp_service.cache_service.get",
        AsyncMock(return_value=sha256_hex(OTP_CODE)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await customer_otp_service.verify_otp(
            OTPVerify(identifier=PHONE, tenant_id=TENANT_ID, otp_code="000000"), session
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "OTP_CODE_INVALID"
    assert exc_info.value.detail["error"]["attempts_remaining"] == 2
    assert customer.otp_attempts == 1


@pytest.mark.asyncio
async def test_verify_otp_third_wrong_attempt_deletes_key_and_forces_new_code(mocker):
    customer = make_customer(otp_attempts=2)
    session = make_session([customer])
    mocker.patch(
        "app.services.customer_otp_service.cache_service.get",
        AsyncMock(return_value=sha256_hex(OTP_CODE)),
    )
    cache_delete = mocker.patch("app.services.customer_otp_service.cache_service.delete", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await customer_otp_service.verify_otp(
            OTPVerify(identifier=PHONE, tenant_id=TENANT_ID, otp_code="000000"), session
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "OTP_TOO_MANY_ATTEMPTS"
    assert customer.otp_attempts == 0  # reset — a new code must be requested
    cache_delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_otp_expired_code_returns_400(mocker):
    customer = make_customer()
    session = make_session([customer])
    mocker.patch("app.services.customer_otp_service.cache_service.get", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        await customer_otp_service.verify_otp(
            OTPVerify(identifier=PHONE, tenant_id=TENANT_ID, otp_code=OTP_CODE), session
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "OTP_CODE_EXPIRED"
