"""QuickBite — Customer JWT, OTP crypto, session cookie helpers.

Implements:
- Customer-specific JWT encode/decode (CUSTOMER_SECRET_KEY, HS256 only)
- The session cookie the token travels in, and how to read it back

OTP generation and hash storage belong to customer_otp_service.py
(NEW-OTP-01/02).

The cookie name and its flags live here rather than in the routers because
they are one contract with two ends: the routes that set the cookie and the
dependency that reads it. They were previously three separate copies of the
string "customer_session" — two setters and no reader — which is exactly how
the write side and the read side drifted apart.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Request, Response

from app.core.config import settings

CUSTOMER_SESSION_COOKIE = "customer_session"


def create_customer_token(customer_id: uuid.UUID, tenant_id: uuid.UUID, phone_hash: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(customer_id),
        "tenant_id": str(tenant_id),
        "phone_hash": phone_hash,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(days=settings.CUSTOMER_JWT_TTL_DAYS),
    }
    return jwt.encode(payload, settings.CUSTOMER_SECRET_KEY, algorithm="HS256")


def decode_customer_token(token: str) -> uuid.UUID | None:
    """Verify a customer JWT and return the customer_id claim, or None if invalid."""
    try:
        payload = jwt.decode(token, settings.CUSTOMER_SECRET_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None


def set_customer_session_cookie(response: Response, token: str) -> None:
    """Attach the customer session cookie (NEW-OTP-02, Doc 3).

    HttpOnly so no script can read the token — which is also why the token is
    never echoed in the JSON body, and why the server must read it back from
    the cookie rather than expecting a client to replay it in a header.

    SameSite=Lax is what stands in for CSRF tokens here: it stops the browser
    attaching this cookie to cross-site POSTs, and every customer-authenticated
    route is a POST. Loosening it to `none` would require real CSRF defence.
    """
    response.set_cookie(
        CUSTOMER_SESSION_COOKIE,
        token,
        max_age=settings.CUSTOMER_JWT_TTL_DAYS * 86400,
        httponly=True,
        secure=settings.ENVIRONMENT != "local",
        samesite="lax",
    )


def read_customer_session_token(request: Request) -> str | None:
    """Pull the customer token from the Authorization header or the cookie.

    Header first, cookie second, on the explicit-beats-ambient principle: a
    caller that deliberately sets a header means it, whereas a cookie rides
    along on every request and could be stale. In practice they never both
    appear — browsers use the cookie (they cannot read the HttpOnly value to
    build a header), native and test clients use the header.
    """
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ")
    return request.cookies.get(CUSTOMER_SESSION_COOKIE)
