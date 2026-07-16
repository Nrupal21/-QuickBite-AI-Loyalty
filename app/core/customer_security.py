"""QuickBite — Customer JWT, OTP crypto, rate limit helpers.

Implements:
- Customer-specific JWT verification (CUSTOMER_SECRET_KEY, HS256 only)

OTP generation (secrets.token_digits(6)), OTP hash storage, and the
encode side of the customer JWT belong to NEW-OTP-01/02/03 and are not
implemented here — LOYALTY-03 only needs to verify a token that may
already be present.
"""

import uuid

import jwt

from app.core.config import settings


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
