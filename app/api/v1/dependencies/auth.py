"""QuickBite — Auth dependencies: resolve_principal, get_current_user, require_role().

Three token families authenticate against this API:

- **local** — HS256, minted by AuthService, carries `jti` for per-session
  revocation and no `iss`.
- **Supabase** — ES256/RS256, verified against the project JWKS.
- **Firebase** — RS256, verified by firebase-admin.

`resolve_principal()` accepts any of them and returns a single `Principal`
carrying real ORM rows. Everything downstream stays provider-blind: notably
`require_role()` is unchanged, because it receives an ordinary `User` and
reads the role level from Postgres exactly as before. Roles asserted by an
external token (`app_metadata.roles` and friends) are ignored outright — the
`identity_links` indirection exists precisely so authorisation never depends
on a claim another system controls.

get_current_user() additionally sets `app.tenant_id` on the DB session (via
app/db/rls.py) so the TENANT-02 RLS policies actually filter — without it an
authenticated request sees zero rows on every RLS-protected table, not other
tenants' rows. It also stashes the decoded claims on `request.state.jwt_claims`
so logout can read `jti`/`exp` without a second decode.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.core.config import settings
from app.core.principal import AuthProvider, Principal, SubjectType
from app.core.security import decode_access_token
from app.db import rls
from app.db.base import get_db
from app.db.models.customer import Customer
from app.db.models.user import Role, User

logger = structlog.get_logger(__name__)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "error": {
            "code": "INVALID_TOKEN",
            "message": "Your session has expired. Please log in again.",
        }
    },
)

_ACCOUNT_DEACTIVATED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "error": {
            "code": "ACCOUNT_DEACTIVATED",
            "message": "This account has been deactivated. Contact your restaurant owner.",
        }
    },
)

_STAFF_REQUIRED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "error": {
            "code": "STAFF_ACCOUNT_REQUIRED",
            "message": "This area is for restaurant staff accounts.",
        }
    },
)

# Each provider may use ONLY these algorithms. Enforcing per-provider — not
# just a global allowlist — is what blocks algorithm confusion: an HS256 token
# claiming the Supabase issuer must never reach the Supabase verifier, where
# the JWKS *public* key would be replayed as an HMAC secret.
_ALG_BY_PROVIDER: dict[AuthProvider, frozenset[str]] = {
    AuthProvider.LOCAL: frozenset({"HS256"}),
    AuthProvider.SUPABASE: frozenset({"ES256", "RS256"}),
    AuthProvider.FIREBASE: frozenset({"RS256"}),
}
_ALLOWED_ALGS = frozenset().union(*_ALG_BY_PROVIDER.values())

# Tolerate this much clock skew when comparing a token's `iat` against the
# revocation watermark. Without it, a container running seconds behind the
# provider produces 401 storms that look exactly like an attack.
_IAT_LEEWAY = timedelta(seconds=30)


def _bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise _UNAUTHORIZED
    return auth_header.removeprefix("Bearer ")


def _classify(token: str) -> AuthProvider:
    """Pick a verifier from the unverified header and `iss`.

    Nothing read here is trusted — it only routes. The chosen verifier
    independently re-pins its algorithms and re-checks `iss`/`aud`/signature.
    """
    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.decode(
            token, options={"verify_signature": False}, algorithms=list(_ALLOWED_ALGS)
        )
    except jwt.InvalidTokenError as exc:
        raise _UNAUTHORIZED from exc

    alg = header.get("alg")
    if alg not in _ALLOWED_ALGS:
        # Covers alg=none and any algorithm we do not deliberately support.
        raise _UNAUTHORIZED
    if "jku" in header or "x5u" in header:
        # These point the verifier at an attacker-chosen key source.
        raise _UNAUTHORIZED

    iss = unverified.get("iss")
    # Exact equality, never startswith/in: a substring test is defeated by an
    # issuer such as "https://evil.com/#https://ref.supabase.co/auth/v1".
    # A provider with empty config can never be selected, so an unconfigured
    # environment cannot be coaxed into that branch.
    if settings.supabase_enabled and iss == settings.supabase_issuer:
        provider = AuthProvider.SUPABASE
    elif settings.firebase_enabled and iss == settings.firebase_issuer:
        provider = AuthProvider.FIREBASE
    elif iss is None:
        # Local tokens carry no issuer (see core/security.py).
        provider = AuthProvider.LOCAL
    else:
        raise _UNAUTHORIZED

    if alg not in _ALG_BY_PROVIDER[provider]:
        raise _UNAUTHORIZED
    return provider


def _assert_not_globally_revoked(subject: User | Customer, claims: dict[str, Any]) -> None:
    """Reject a token issued before the subject's revocation watermark.

    Costs no query — `tokens_valid_from` is on the row we already loaded. This
    is the only revocation mechanism that reaches Supabase and Firebase
    sessions, since neither exposes a server-side per-session handle; it is
    applied to local tokens too, as defence in depth behind the jti blocklist.
    """
    iat = claims.get("iat")
    if iat is None:
        # Every provider sets iat. Its absence is either a forgery or a token
        # we cannot reason about — either way, fail closed.
        raise _UNAUTHORIZED
    valid_from = subject.tokens_valid_from
    if valid_from is None:  # pragma: no cover - server_default backfills every row
        return
    if valid_from.tzinfo is None:
        valid_from = valid_from.replace(tzinfo=timezone.utc)
    issued = datetime.fromtimestamp(int(iat), tz=timezone.utc)
    if issued + _IAT_LEEWAY < valid_from:
        raise _UNAUTHORIZED


async def _resolve_local(session: AsyncSession, token: str) -> Principal:
    """Verify a locally-minted HS256 token.

    The query order here is contractual: `select(User)` must be the first
    `session.execute` and `set_config` the second (AUTH-04 tests pin those
    positions, and more importantly the tenant must be bound before anything
    else reads an RLS table). Nothing may be inserted between them.
    """
    claims = decode_access_token(token)
    if claims is None:
        raise _UNAUTHORIZED

    jti = claims.get("jti")
    if jti and await cache_service.exists(f"revoked_jti:{jti}"):
        raise _UNAUTHORIZED

    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise _UNAUTHORIZED from exc

    # Bound from the claim, before the row read, because under row-level
    # security an unscoped `select(User)` returns nothing at all (migration
    # 0006/0007) and every authenticated request would 401.
    #
    # Trusting the claim for *scoping* is safe in a way that trusting it for
    # authorisation would not be: we signed this token with SECRET_KEY, so the
    # value is authentic, and it is only ever used to narrow the search. A
    # tampered tenant_id simply fails to find the user and 401s. Authorisation
    # still derives from `user.tenant_id` on the row below, per Principal's
    # invariant that tenant identity comes from the database and never a claim.
    try:
        claim_tenant_id = uuid.UUID(claims["tenant_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise _UNAUTHORIZED from exc

    # `true` = local to this transaction, matching the per-request session
    # lifecycle in get_db() — never leaks tenant context across requests.
    await rls.set_tenant_context(session, claim_tenant_id)

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise _UNAUTHORIZED

    # An access token stays signed and unexpired for its full TTL, so a
    # deactivated member would keep working until it lapsed. Checking the row
    # on every request closes that window immediately (AUTH-04 team removal).
    if not user.is_active:
        raise _ACCOUNT_DEACTIVATED

    _assert_not_globally_revoked(user, claims)

    return Principal(
        subject_type=SubjectType.USER,
        tenant_id=user.tenant_id,
        auth_provider=AuthProvider.LOCAL,
        provider_subject=str(user.id),
        claims=claims,
        user=user,
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=timezone.utc)
        if claims.get("iat")
        else None,
    )


async def _resolve_external(
    request: Request,
    session: AsyncSession,
    token: str,
    provider: AuthProvider,
) -> Principal:
    """Verify a Supabase or Firebase token and map it onto a local principal."""
    # Imported here rather than at module scope: identity_link_service imports
    # this module's error constants, so a top-level import would cycle.
    from app.services import identity_link_service  # noqa: PLC0415

    if provider is AuthProvider.SUPABASE:
        from app.core.supabase_auth import verify_supabase_token  # noqa: PLC0415

        claims = await verify_supabase_token(token)
    else:
        from app.core.firebase_auth import verify_firebase_token  # noqa: PLC0415

        claims = await verify_firebase_token(token)

    subject = claims.get("sub")
    if not subject:
        raise _UNAUTHORIZED

    principal = await identity_link_service.resolve(
        request=request,
        session=session,
        provider=provider,
        subject=str(subject),
        claims=claims,
    )
    _assert_not_globally_revoked(principal.user or principal.customer, claims)
    return principal


async def resolve_principal(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Principal:
    """Authenticate a request against whichever of the three providers issued
    its bearer token, and bind the tenant for RLS.

    Every failure path returns the same 401 body. A differentiated message
    ("unknown issuer" vs "bad signature") is a probing oracle that tells an
    attacker which providers are enabled and how far their token got.
    """
    token = _bearer_token(request)
    provider = _classify(token)

    if provider is AuthProvider.LOCAL:
        principal = await _resolve_local(session, token)
    else:
        principal = await _resolve_external(request, session, token, provider)

    request.state.principal = principal
    request.state.jwt_claims = principal.claims
    return principal


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> User:
    """The authenticated staff/owner User. 403 for a customer principal.

    Signature is deliberately unchanged (`request` first, positional) — routes
    and the AUTH-04 tests call it directly.
    """
    principal = await resolve_principal(request, session)
    if principal.subject_type is not SubjectType.USER or principal.user is None:
        # A customer token is a valid credential for the wrong surface. 403,
        # not 401: re-authenticating would not help.
        raise _STAFF_REQUIRED
    return principal.user


def require_role(min_level: int):
    """Dependency factory — 403 unless the caller's role.level <= min_level.

    Role.level is a rank, not a count: 1=Super Admin, 2=Owner, 3=Manager,
    4=Staff, so a *lower* number is *more* privileged. `min_level` is really
    "the least-senior level this endpoint accepts" — `require_role(RoleLevel.MANAGER)`
    admits Super Admin, Owner, and Manager, and 403s Staff.

    Provider-agnostic by construction: it reads the role from Postgres via the
    User row, so a Supabase- or Firebase-authenticated staff member is
    authorised identically to a locally-authenticated one.
    """

    async def _dependency(
        current_user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> User:
        result = await session.execute(select(Role.level).where(Role.id == current_user.role_id))
        role_level = result.scalar_one_or_none()
        if role_level is None or role_level > min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "INSUFFICIENT_PERMISSIONS",
                        "message": "You don't have permission to do that.",
                    }
                },
            )
        return current_user

    return _dependency
