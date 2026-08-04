"""Supabase Auth access-token verification (asymmetric, JWKS-backed).

Supabase issues ES256/RS256 JWTs whose public keys are published at the
project's JWKS endpoint. Verification is fully local once the key is cached —
no call to Supabase per request.

Legacy HS256 projects (a shared `JWT_SECRET` also held by the client-facing
API) are deliberately unsupported. Supabase's own documentation recommends
against them, and accepting HS256 here would mean the same key both signs and
verifies while also colliding with local-token classification. Enable JWT
Signing Keys under Dashboard > Settings > API instead.
"""

from typing import Any

import jwt
import structlog
from fastapi import HTTPException, status

from app.core import jwks_cache
from app.core.config import settings

logger = structlog.get_logger(__name__)

# Supabase sets `aud` to the literal string "authenticated" for signed-in end
# users — NOT the project ref, which is the intuitive-but-wrong guess. Getting
# it wrong rejects everything, and "fixing" that with verify_aud=False would
# accept tokens minted for a different audience entirely.
_EXPECTED_AUDIENCE = "authenticated"
_ALGORITHMS = ["ES256", "RS256"]
# Container clocks drift. Without leeway, a few seconds of skew produces a
# storm of 401s that looks exactly like an attack.
_LEEWAY_SECONDS = 30

_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "error": {
            "code": "INVALID_TOKEN",
            "message": "Your session has expired. Please log in again.",
        }
    },
)


async def verify_supabase_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token and return its claims.

    Raises 401 for anything unverifiable, 503 (from jwks_cache) when the
    provider is unreachable and no keys are cached.
    """
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.InvalidTokenError as exc:
        raise _INVALID from exc
    if not kid:
        # Every Supabase JWT carries a kid. Its absence means either a legacy
        # HS256 token or a forgery; neither should authenticate.
        raise _INVALID

    signing_key = await jwks_cache.get_signing_key(kid)

    try:
        claims = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=_ALGORITHMS,
            audience=_EXPECTED_AUDIENCE,
            issuer=settings.supabase_issuer,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.InvalidTokenError as exc:
        logger.info("auth.supabase.token_rejected", reason=type(exc).__name__)
        raise _INVALID from exc

    # A `service_role` JWT is signed by the same key, carries the same issuer,
    # and grants unrestricted database access with RLS bypassed. It is an API
    # key that people paste into .env files and CI logs — it must never
    # authenticate as an end user. `anon` is likewise not a person.
    if claims.get("role") != "authenticated":
        logger.warning("auth.supabase.non_user_role_rejected", role=claims.get("role"))
        raise _INVALID

    return claims
