"""QuickBite — Owner/Staff JWT, bcrypt, TOTP, refresh rotation.

Implements:
- bcrypt hash/verify with work factor from settings (>= 12, validated in config)
- JWT encode/decode with HS256 only (never 'none'), signed with SECRET_KEY —
  customer loyalty JWTs use CUSTOMER_SECRET_KEY in customer_security.py
- Opaque verification tokens for email verification links, MFA session
  tokens (AUTH-02), and refresh tokens (AUTH-03)
- TOTP verification (AUTH-02) — RFC 6238 via pyotp
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pyotp
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(
    schemes=["bcrypt"], bcrypt__rounds=settings.BCRYPT_ROUNDS, deprecated="auto"
)

# Hashed once at import time so a login for an unknown email still pays the
# bcrypt cost against a real hash of this build's work factor — no timing
# oracle that reveals whether the email is registered.
_DUMMY_PASSWORD_HASH = _pwd_context.hash("no-account-uses-this-password")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _pwd_context.verify(password, hashed)


def verify_password_constant_time(password: str, hashed: str | None) -> bool:
    """Like verify_password, but always pays the bcrypt cost even when `hashed`
    is None (unknown email) — prevents a timing oracle revealing whether an
    email is registered."""
    matches = _pwd_context.verify(password, hashed or _DUMMY_PASSWORD_HASH)
    return matches if hashed is not None else False


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


def generate_mfa_session_token() -> str:
    """Opaque token issued after password check, exchanged for a JWT via TOTP."""
    return secrets.token_urlsafe(32)


def create_refresh_token() -> str:
    """Opaque single-use refresh token (384-bit). Caller stores its SHA-256 hash."""
    return secrets.token_urlsafe(48)


def verify_totp_code(secret: str, code: str) -> bool:
    """RFC 6238 TOTP check with a +/-1 step window to tolerate clock drift."""
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def generate_totp_secret() -> str:
    """Fresh base32 TOTP secret (160-bit, per RFC 4226 §4 R6).

    Caller must encrypt it with AES-256-GCM before it reaches the DB — a
    plaintext `users.totp_secret` column would let anyone with read access
    mint valid second factors.
    """
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, account_name: str, issuer: str = "QuickBite") -> str:
    """`otpauth://` URI for the enrollment QR code (Google Authenticator format)."""
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)
