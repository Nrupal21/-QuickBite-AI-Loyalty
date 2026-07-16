"""QuickBite — Owner/Staff JWT, bcrypt, TOTP, refresh rotation.

Implements:
- bcrypt hash/verify with work factor from settings (>= 12, validated in config)
- JWT encode/decode with HS256 only (never 'none'), signed with SECRET_KEY —
  customer loyalty JWTs use CUSTOMER_SECRET_KEY in customer_security.py
- Opaque verification tokens for email verification links

TOTP MFA and refresh-token rotation arrive with AUTH-02/AUTH-03.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(
    schemes=["bcrypt"], bcrypt__rounds=settings.BCRYPT_ROUNDS, deprecated="auto"
)


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _pwd_context.verify(password, hashed)


def create_access_token(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Verify an owner/staff JWT. Returns the claims dict, or None if invalid."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


def generate_verification_token() -> str:
    """Opaque single-use token for email verification links (256-bit)."""
    return secrets.token_urlsafe(32)
