"""Unit tests for the customer session cookie — set side and read side (NEW-OTP-02).

These exist because the two ends of this contract drifted apart unnoticed:
/auth/customer/otp-verify and /customers/register set an HttpOnly cookie and
keep the token out of the JSON body, while the dependency read only an
Authorization header. A browser therefore had no way to authenticate at all.

It failed silently rather than loudly: the only consumer is
get_current_customer_optional, so every authenticated scan degraded to
anonymous — no stamp increment, no customer_id on the StampLog — instead of
returning 401. The service-layer tests all passed throughout, because nothing
covered the route's cookie or the dependency's token source.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response

from app.api.v1.dependencies.customer_auth import (
    get_current_customer,
    get_current_customer_optional,
)
from app.core.config import settings
from app.core.customer_security import (
    CUSTOMER_SESSION_COOKIE,
    create_customer_token,
    read_customer_session_token,
    set_customer_session_cookie,
)
from app.core.encryption import encrypt_pii, sha256_hex
from app.db.base import get_db
from app.db.models.customer import Customer
from app.main import app
from app.schemas.customer_auth import OTPVerifiedResponse
from app.schemas.loyalty import ScanResponse

TENANT_ID = uuid.uuid4()
PHONE = "+919876543210"


def make_customer(**overrides) -> Customer:
    defaults = {
        "tenant_id": TENANT_ID,
        "phone_hash": sha256_hex(PHONE),
        "encrypted_phone": encrypt_pii(PHONE),
        "total_stamps_alltime": 3,
        "current_reward_count": 3,
        "is_blocked": False,
        "otp_attempts": 0,
    }
    defaults.update(overrides)
    customer = Customer(**defaults)
    customer.id = uuid.uuid4()
    return customer


def make_session(customer: Customer | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = customer
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


def make_request(*, header_token: str | None = None, cookie_token: str | None = None) -> MagicMock:
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {header_token}"} if header_token else {}
    request.cookies = {CUSTOMER_SESSION_COOKIE: cookie_token} if cookie_token else {}
    request.state = MagicMock()
    return request


# --- set side -----------------------------------------------------------


def test_cookie_is_httponly_and_samesite_lax():
    """HttpOnly is why the token is not echoed in the body; Lax is what stands
    in for CSRF tokens, since every customer-authenticated route is a POST."""
    response = Response()

    set_customer_session_cookie(response, "customer-jwt-abc")

    header = response.headers["set-cookie"]
    assert header.startswith(f"{CUSTOMER_SESSION_COOKIE}=customer-jwt-abc")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert f"Max-Age={settings.CUSTOMER_JWT_TTL_DAYS * 86400}" in header


def test_cookie_is_not_secure_only_in_local(mocker):
    mocker.patch.object(settings, "ENVIRONMENT", "local")
    local = Response()
    set_customer_session_cookie(local, "t")
    assert "secure" not in local.headers["set-cookie"].lower()

    mocker.patch.object(settings, "ENVIRONMENT", "production")
    prod = Response()
    set_customer_session_cookie(prod, "t")
    assert "secure" in prod.headers["set-cookie"].lower()


# --- read side ----------------------------------------------------------


def test_reads_token_from_cookie():
    assert read_customer_session_token(make_request(cookie_token="abc")) == "abc"


def test_reads_token_from_authorization_header():
    assert read_customer_session_token(make_request(header_token="abc")) == "abc"


def test_header_wins_over_cookie():
    """Explicit beats ambient: a caller that sets a header meant it, whereas a
    cookie rides along on every request and may be stale."""
    request = make_request(header_token="from-header", cookie_token="from-cookie")

    assert read_customer_session_token(request) == "from-header"


def test_no_token_anywhere_returns_none():
    assert read_customer_session_token(make_request()) is None


def test_non_bearer_authorization_falls_through_to_cookie():
    request = MagicMock()
    request.headers = {"Authorization": "Basic dXNlcjpwYXNz"}
    request.cookies = {CUSTOMER_SESSION_COOKIE: "cookie-token"}

    assert read_customer_session_token(request) == "cookie-token"


# --- end to end through the dependency ----------------------------------


@pytest.mark.asyncio
async def test_cookie_from_otp_verify_authenticates_the_scan(mocker):
    """The regression this file exists for: the exact token otp-verify puts in
    the cookie must resolve to a Customer on the next request."""
    customer = make_customer()
    token = create_customer_token(customer.id, TENANT_ID, customer.phone_hash)
    mocker.patch("app.api.v1.dependencies.customer_auth.rls.set_tenant_context", AsyncMock())

    resolved = await get_current_customer_optional(
        make_request(cookie_token=token), make_session(customer)
    )

    assert resolved is customer


@pytest.mark.asyncio
async def test_cookie_binds_tenant_context_for_rls(mocker):
    """Without this the customer's own rows are invisible to them — every
    RLS-protected table returns zero rows."""
    customer = make_customer()
    token = create_customer_token(customer.id, TENANT_ID, customer.phone_hash)
    set_tenant = mocker.patch(
        "app.api.v1.dependencies.customer_auth.rls.set_tenant_context", AsyncMock()
    )
    session = make_session(customer)

    await get_current_customer_optional(make_request(cookie_token=token), session)

    set_tenant.assert_awaited_once()
    assert set_tenant.await_args.args[1] == TENANT_ID


@pytest.mark.asyncio
async def test_required_dependency_accepts_the_cookie(mocker):
    customer = make_customer()
    token = create_customer_token(customer.id, TENANT_ID, customer.phone_hash)
    mocker.patch("app.api.v1.dependencies.customer_auth.rls.set_tenant_context", AsyncMock())

    resolved = await get_current_customer(
        make_request(cookie_token=token), make_session(customer)
    )

    assert resolved is customer


@pytest.mark.asyncio
async def test_tampered_cookie_is_anonymous_not_authenticated(mocker):
    """A forged cookie must not authenticate — and the optional dependency must
    still not raise, since anonymous scans are allowed."""
    resolved = await get_current_customer_optional(
        make_request(cookie_token="not.a.jwt"), make_session(None)
    )

    assert resolved is None


@pytest.mark.asyncio
async def test_cookie_signed_with_the_owner_key_is_rejected(mocker):
    """SECRET_KEY and CUSTOMER_SECRET_KEY are separate for exactly this reason:
    an owner token must never open a customer session."""
    import jwt

    owner_token = jwt.encode(
        {"sub": str(uuid.uuid4()), "tenant_id": str(TENANT_ID)},
        settings.SECRET_KEY,
        algorithm="HS256",
    )

    resolved = await get_current_customer_optional(
        make_request(cookie_token=owner_token), make_session(None)
    )

    assert resolved is None


@pytest.mark.asyncio
async def test_no_credentials_raises_401_on_the_required_dependency():
    with pytest.raises(HTTPException) as exc_info:
        await get_current_customer(make_request(), make_session(None))

    assert exc_info.value.status_code == 401


# --- the seam, through the real ASGI stack ------------------------------


@pytest.mark.asyncio
async def test_otp_verify_cookie_round_trips_to_an_authenticated_scan(mocker, client):
    """The end-to-end path that was broken: log in, then scan as that customer.

    Exercises both real routes and the real dependency, so it fails if the two
    ends of the cookie contract ever drift apart again — which is precisely
    what the unit tests above could not catch on their own.
    """
    customer = make_customer()
    token = create_customer_token(customer.id, TENANT_ID, customer.phone_hash)
    session = make_session(customer)

    app.dependency_overrides[get_db] = lambda: session
    mocker.patch(
        "app.api.v1.routers.customer_auth.customer_otp_service.verify_otp",
        AsyncMock(
            return_value=(
                OTPVerifiedResponse(
                    customer_id=str(customer.id),
                    total_stamps_alltime=customer.total_stamps_alltime,
                    current_reward_count=customer.current_reward_count,
                ),
                token,
            )
        ),
    )
    mocker.patch("app.api.v1.dependencies.customer_auth.rls.set_tenant_context", AsyncMock())
    scan_mock = mocker.patch(
        "app.api.v1.routers.loyalty.LoyaltyService.process_scan",
        AsyncMock(
            return_value=ScanResponse(
                stamp_count=4,
                reward_progress=0.4,
                reward_unlocked=False,
                redemption_code=None,
                next_reward_at=None,
            )
        ),
    )

    try:
        verify = await client.post(
            "/api/v1/auth/customer/otp-verify",
            json={
                "identifier": PHONE,
                "tenant_id": str(TENANT_ID),
                "otp_code": "482913",
            },
        )
        assert verify.status_code == 200
        # HttpOnly cookie set, and the token deliberately absent from the body.
        assert CUSTOMER_SESSION_COOKIE in verify.cookies
        assert token not in verify.text

        # httpx replays the cookie exactly as a browser would.
        scan = await client.post(
            "/api/v1/loyalty/scan",
            json={"qr_token": "qr-token-marcos-bandra", "gps_lat": 19.07, "gps_lng": 72.87},
        )
        assert scan.status_code == 200
    finally:
        app.dependency_overrides.clear()

    # The scan was attributed to the customer, not treated as anonymous.
    assert scan_mock.await_args.kwargs["customer"] is customer
