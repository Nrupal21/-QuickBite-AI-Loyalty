"""SEC-19 — OTP Attack Penetration Tests.

Executes `docs/PENTEST_PLAN.md` §2 against `customer_otp_service`. The
functional brute-force/replay/daily-limit cases already have unit coverage
in `tests/unit/test_customer_otp.py` (written for NEW-OTP-01/02's acceptance
criteria); this file exists as SEC-19's own attacker-framed evidence and adds
the one case that file does not cover: enumeration via response shape across
*both* request and verify.

Timing-based enumeration (docs/PENTEST_PLAN.md §2.3) is explicitly **not**
asserted here — a `< 50ms` latency-difference bound is not a meaningful
unit-test assertion (mocked Redis/DB calls have ~0ms latency either way, so a
mock-based test proves nothing about it). That case requires a scripted
timing harness run against a real staging deployment, tracked as an open
SEC-19 execution item in docs/PENTEST_PLAN.md.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.encryption import sha256_hex
from app.db.models.customer import Customer
from app.schemas.customer_auth import OTPRequest, OTPVerify
from app.services import customer_otp_service

TENANT_ID = uuid.uuid4()
PHONE_REGISTERED = "+919876543210"
PHONE_UNREGISTERED = "+919876500000"


def make_customer(**overrides) -> Customer:
    defaults = {
        "tenant_id": TENANT_ID,
        "phone_hash": sha256_hex(PHONE_REGISTERED),
        "otp_attempts": 0,
    }
    defaults.update(overrides)
    customer = Customer(**defaults)
    customer.id = uuid.uuid4()
    return customer


def make_session(result_value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = result_value
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


@pytest.fixture(autouse=True)
def no_real_rls(mocker):
    mocker.patch("app.services.customer_otp_service.rls.set_tenant_context", AsyncMock())


# --- 2.1 brute-force lockout ----------------------------------------------


@pytest.mark.asyncio
async def test_three_wrong_codes_locks_and_purges_the_otp(mocker):
    customer = make_customer(otp_attempts=2)
    session = make_session(customer)
    mocker.patch("app.services.customer_otp_service.cache_service.get", AsyncMock(return_value="correct-hash"))
    delete_mock = mocker.patch("app.services.customer_otp_service.cache_service.delete", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await customer_otp_service.verify_otp(
            OTPVerify(identifier=PHONE_REGISTERED, tenant_id=TENANT_ID, otp_code="000000"), session
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "OTP_TOO_MANY_ATTEMPTS"
    delete_mock.assert_awaited_once()  # the key is purged — no 4th guess against the same code


# --- 2.2 replay after expiry ------------------------------------------------


@pytest.mark.asyncio
async def test_code_replay_after_ttl_expiry_returns_400(mocker):
    """The Redis key TTL'd out — attacker replays a code that *was* correct a
    moment ago. Simulated as `cache_service.get` returning None (expired)."""
    customer = make_customer()
    session = make_session(customer)
    mocker.patch("app.services.customer_otp_service.cache_service.get", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        await customer_otp_service.verify_otp(
            OTPVerify(identifier=PHONE_REGISTERED, tenant_id=TENANT_ID, otp_code="123456"), session
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "OTP_CODE_EXPIRED"


# --- 2.4 enumeration via response shape (request + verify) -----------------


@pytest.mark.asyncio
async def test_unregistered_identifier_request_never_404s(mocker):
    session = make_session(None)  # no Customer row
    mocker.patch("app.services.customer_otp_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.services.customer_otp_service.cache_service.incr", AsyncMock(return_value=1))
    mocker.patch("app.services.customer_otp_service.cache_service.set", AsyncMock())

    response = await customer_otp_service.request_otp(
        OTPRequest(identifier=PHONE_UNREGISTERED, tenant_id=TENANT_ID), session
    )

    # 200 either way — a probing attacker cannot distinguish "registered" from
    # "unregistered" via HTTP status. The *shape* legitimately differs
    # (registration_token vs. nothing), which is required UX, not a leak: it
    # tells the caller "you need to register", never "this phone has an
    # account with us".
    assert response.registration_token


@pytest.mark.asyncio
async def test_unregistered_identifier_verify_returns_same_error_as_expired_code():
    """verify_otp must not distinguish "no such customer" from "code expired"
    — both are the generic 400 OTP_CODE_EXPIRED, so a scripted attacker probing
    /otp-verify with random identifiers cannot use it to enumerate accounts."""
    session = make_session(None)

    with pytest.raises(HTTPException) as exc_info:
        await customer_otp_service.verify_otp(
            OTPVerify(identifier=PHONE_UNREGISTERED, tenant_id=TENANT_ID, otp_code="123456"), session
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "OTP_CODE_EXPIRED"


# --- 2.5 daily limit bypass -------------------------------------------------


@pytest.mark.asyncio
async def test_sixth_daily_request_is_rate_limited(mocker):
    session = make_session(make_customer())
    mocker.patch("app.services.customer_otp_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch(
        "app.services.customer_otp_service.cache_service.incr",
        AsyncMock(return_value=settings.CUSTOMER_OTP_DAILY_MAX + 1),
    )

    with pytest.raises(HTTPException) as exc_info:
        await customer_otp_service.request_otp(
            OTPRequest(identifier=PHONE_REGISTERED, tenant_id=TENANT_ID), session
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "OTP_DAILY_LIMIT"


# --- 2.6 cooldown bypass attempt --------------------------------------------


@pytest.mark.asyncio
async def test_request_within_cooldown_window_is_rejected(mocker):
    session = make_session(make_customer())
    mocker.patch("app.services.customer_otp_service.cache_service.exists", AsyncMock(return_value=True))

    with pytest.raises(HTTPException) as exc_info:
        await customer_otp_service.request_otp(
            OTPRequest(identifier=PHONE_REGISTERED, tenant_id=TENANT_ID), session
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "OTP_RATE_LIMIT"
    assert exc_info.value.headers["Retry-After"] == str(settings.CUSTOMER_OTP_RATE_LIMIT_SECONDS)


# --- 2.7 daily-limit evasion via identifier cycling (unmitigated gap) -------


@pytest.mark.asyncio
async def test_daily_limit_does_not_throttle_across_different_identifiers(mocker):
    """A real, currently-unmitigated finding, not a false negative in this
    test: rate limits here are keyed per-identifier only
    (`otp_cooldown:{tenant_id}:{identifier_hash}`,
    `otp_daily:{tenant_id}:{identifier_hash}`). An attacker cycling through
    many *different* phone numbers/emails in a burst is not throttled by any
    per-IP or global mechanism in this service.

    This test intentionally asserts the attack SUCCEEDS today — both
    identifiers get a fresh per-identifier allowance and both requests go
    through. That is the finding: file it against SEC-24 (severity: Medium,
    cost/harassment vector), don't read a green check here as "no gap
    exists". Once an IP/global limit ships, invert this test to assert the
    second identifier is throttled too.
    """
    mocker.patch("app.services.customer_otp_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.services.customer_otp_service.cache_service.incr", AsyncMock(return_value=1))
    mocker.patch("app.services.customer_otp_service.cache_service.set", AsyncMock())

    response_a = await customer_otp_service.request_otp(
        OTPRequest(identifier="+919876500001", tenant_id=TENANT_ID), make_session(None)
    )
    response_b = await customer_otp_service.request_otp(
        OTPRequest(identifier="+919876500002", tenant_id=TENANT_ID), make_session(None)
    )

    assert response_a.registration_token
    assert response_b.registration_token
