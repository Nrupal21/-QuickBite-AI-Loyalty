"""QuickBite — Social sign-in and sign-up for `restaurant.users`.

The third route on the unified auth surface, alongside password
(AuthService) and one-time code (user_otp_service). Firebase's client SDK
folds Google, Apple, Microsoft, GitHub, and Twitter/X into a single ID token
shape, so there is one verification path here regardless of which button was
pressed — no per-provider branching and no `provider` field on the request.

## Why this exists next to identity_link_service, not inside it

`identity_link_service` states a hard rule: **staff accounts are never
created just-in-time**, because minting a `restaurant.users` row on first
contact would be open registration *with a role attached*, and the tenant
would be a guess. That rule is still correct and still enforced — for the
account shapes it was written about.

It does not bind here, because both of its premises are gone. The account
this module creates has role USER and `tenant_id NULL`: no role worth
attaching and no tenant to guess. It is exactly what the password and OTP
routes already create for anyone who signs up, and it stays that way until
the holder explicitly registers a business through "Join Us", which mints
the tenant and the OWNER role in one audited transaction.

So: a social sign-in may create a *standard user*. It may never create, or
attach itself to, an account that already carries a tenant or a privileged
role — see the guard in `_link_existing_account`.

## Where the email claim is trusted, and where it is not

Only when the provider marks it verified. An unverified email is the
provider's unbacked assertion about an address someone else may own;
treating it as a join key lets an attacker register a victim's address
upstream and inherit their local account. `_verified_email` is the single
place that judgement is made.

Even verified, the email is a *bootstrap* key only: the first successful
sign-in writes an `identity_links` row, and every sign-in after that resolves
through the provider subject, which no one can re-register.
"""

import json
import uuid

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.core.encryption import decrypt_pii, encrypt_pii, sha256_hex
from app.core.firebase_auth import verify_firebase_token
from app.core.principal import AuthProvider, SubjectType
from app.core.security import generate_password_reset_token
from app.db import rls
from app.db.models.audit import AuditLog
from app.db.models.identity_link import IdentityLink
from app.db.models.user import Role, User
from app.schemas.auth import (
    CompleteOAuthRegistrationRequest,
    MFAChallengeResponse,
    MFAEnrollmentRequiredResponse,
    OAuthRegistrationRequiredResponse,
    TokenResponse,
    UserOAuthSignInRequest,
)
from app.services import identity_link_service, messaging_service
from app.services.auth_service import AuthService

logger = structlog.get_logger(__name__)

# Matches user_otp_service.REGISTRATION_TOKEN_TTL_SECONDS — the same "finish
# your sign-up" window, reached from a verified provider subject instead of a
# verified code.
REGISTRATION_TOKEN_TTL_SECONDS = 900

_INVALID_TOKEN = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "error": {
            "code": "INVALID_TOKEN",
            "message": "That sign-in could not be verified. Please try again.",
        }
    },
)


def _verified_email(claims: dict) -> str | None:
    """The email claim, but only when the provider vouches for it.

    Mirrors identity_link_service._verified_email and
    customer_oauth_service._verified_email — deliberately a third copy rather
    than a shared import, because each is a security judgement its own module
    is responsible for, and a future change to one must not silently change
    what the other two trust.
    """
    email = claims.get("email")
    if email and claims.get("email_verified") is True:
        return email.strip().lower()
    return None


async def _find_linked_user(session: AsyncSession, subject: str) -> User | None:
    """Resolve a Firebase subject to a User through `identity_links`.

    `identity_links` is the bootstrap table (no tenant is known yet) and
    `users` is RLS-protected with no principal bound, so both reads run under
    the admin bypass — the same reasoning user_otp_service._find_user carries.
    """
    subject_h = identity_link_service.subject_hash(AuthProvider.FIREBASE, subject)
    async with rls.admin_bypass_context(session):
        link = (
            await session.execute(
                select(IdentityLink).where(
                    IdentityLink.provider == AuthProvider.FIREBASE.value,
                    IdentityLink.provider_subject_hash == subject_h,
                    IdentityLink.subject_type == SubjectType.USER.value,
                    IdentityLink.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if link is None:
            return None
        return (
            await session.execute(select(User).where(User.id == link.local_id))
        ).scalar_one_or_none()


async def _find_user_by_email(session: AsyncSession, email: str) -> User | None:
    async with rls.admin_bypass_context(session):
        return (
            await session.execute(
                select(User).where(User.email_hash == sha256_hex(email))
            )
        ).scalar_one_or_none()


async def sign_in(
    payload: UserOAuthSignInRequest, session: AsyncSession, ip_address_hash: str
) -> (
    MFAChallengeResponse
    | MFAEnrollmentRequiredResponse
    | TokenResponse
    | OAuthRegistrationRequiredResponse
):
    """Verify the provider token, then sign in or offer to finish signing up.

    Three outcomes, in the order they are tried:

    1. The subject is already linked → sign that account in.
    2. The subject is new but its *verified* email matches an account → link
       the two, then sign in. This is the one-time bootstrap that turns an
       email into a durable subject link.
    3. Neither → hand back a registration token; the caller confirms a display
       name and `complete_registration` creates the account.

    Every signed-in outcome goes through AuthService.complete_authentication,
    so an Owner arriving through a Google button meets the same mandatory-MFA
    gate as one arriving through the password form.
    """
    claims = await verify_firebase_token(payload.id_token)
    subject = claims.get("sub")
    if not subject:
        raise _INVALID_TOKEN
    subject = str(subject)

    user = await _find_linked_user(session, subject)
    if user is not None:
        logger.info("auth.user_oauth.linked_sign_in", user_id=str(user.id))
        return await AuthService(session=session).complete_authentication(
            user, ip_address_hash, method="oauth"
        )

    email = _verified_email(claims)
    if email is not None:
        existing = await _find_user_by_email(session, email)
        if existing is not None:
            await _link_existing_account(session, existing, subject, ip_address_hash)
            logger.info("auth.user_oauth.email_bootstrap", user_id=str(existing.id))
            return await AuthService(session=session).complete_authentication(
                existing, ip_address_hash, method="oauth"
            )

    registration_token = generate_password_reset_token()
    await cache_service.set(
        f"user_oauth_reg:{registration_token}",
        json.dumps(
            {
                "provider": AuthProvider.FIREBASE.value,
                "encrypted_subject": encrypt_pii(subject),
                "verified_email": email,
            }
        ),
        ttl=REGISTRATION_TOKEN_TTL_SECONDS,
    )
    logger.info("auth.user_oauth.registration_required", has_verified_email=email is not None)
    return OAuthRegistrationRequiredResponse(
        registration_token=registration_token,
        email=email,
        suggested_name=(claims.get("name") or "").strip() or None,
        expires_in=REGISTRATION_TOKEN_TTL_SECONDS,
    )


async def _link_existing_account(
    session: AsyncSession, user: User, subject: str, ip_address_hash: str
) -> None:
    """Bind a verified provider subject to an account matched by email.

    Refused for any account that already carries a tenant or a privileged
    role. That is identity_link_service's rule held intact: an Owner's or
    Manager's account is attached to an external identity only through
    `POST /auth/link/{provider}`, which demands a live local session as proof
    of who the caller already is. A verified email alone must never be enough
    to walk into a restaurant's dashboard — it is one password reset at the
    provider away from being someone else's.
    """
    if user.tenant_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "IDENTITY_LINK_REQUIRED",
                    "message": (
                        "Sign in with your password first, then link this "
                        "account from your profile."
                    ),
                }
            },
        )

    session.add(
        IdentityLink(
            provider=AuthProvider.FIREBASE.value,
            provider_subject_hash=identity_link_service.subject_hash(
                AuthProvider.FIREBASE, subject
            ),
            encrypted_provider_subject=encrypt_pii(subject),
            subject_type=SubjectType.USER.value,
            local_id=user.id,
            tenant_id=None,
            linked_via=identity_link_service.LINKED_VIA_EMAIL,
        )
    )
    session.add(
        AuditLog(
            tenant_id=None,
            user_id=user.id,
            action="identity_link_created",
            resource_type="identity_link",
            ip_address_hash=ip_address_hash,
            event_metadata={
                "provider": AuthProvider.FIREBASE.value,
                "linked_via": identity_link_service.LINKED_VIA_EMAIL,
            },
        )
    )
    await session.flush()


async def complete_registration(
    payload: CompleteOAuthRegistrationRequest,
    session: AsyncSession,
    ip_address_hash: str,
    account_url: str,
) -> TokenResponse:
    """Create the standard account a verified provider subject earned.

    The subject and email come from the server-side token, never the request
    body — the caller supplies only a display name. A client that could name
    its own email here could register any address it liked against a subject
    that never proved it.
    """
    raw = await cache_service.get(f"user_oauth_reg:{payload.registration_token}")
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "REGISTRATION_EXPIRED",
                    "message": "That sign-up expired. Sign in with your provider again.",
                }
            },
        )
    pending = json.loads(raw)
    subject = decrypt_pii(pending["encrypted_subject"])
    email = pending.get("verified_email")

    # Re-checked after the token was minted: the same person may have signed
    # up through another route in the intervening fifteen minutes, and the
    # unique index on email_hash would otherwise surface as a 500.
    if email is not None and await _find_user_by_email(session, email) is not None:
        await cache_service.delete(f"user_oauth_reg:{payload.registration_token}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "ALREADY_REGISTERED",
                    "message": "An account with this email already exists. Try signing in.",
                }
            },
        )

    role = await _get_user_role(session)
    user = User(
        id=uuid.uuid4(),
        tenant_id=None,
        role_id=role.id,
        email_hash=sha256_hex(email) if email else None,
        encrypted_email=encrypt_pii(email) if email else None,
        # No password: a social account has none to choose. The holder can add
        # one later from their profile (migration 0014 makes the column
        # nullable for exactly this).
        hashed_password=None,
        # The provider verified the address before it was ever handed over; a
        # subject with no verified email has no email to leave unverified.
        email_verified=True,
    )
    session.add(user)
    # audit_logs.user_id and identity_links.local_id both point at this row.
    await session.flush()

    session.add(
        IdentityLink(
            provider=AuthProvider.FIREBASE.value,
            provider_subject_hash=identity_link_service.subject_hash(
                AuthProvider.FIREBASE, subject
            ),
            encrypted_provider_subject=encrypt_pii(subject),
            subject_type=SubjectType.USER.value,
            local_id=user.id,
            tenant_id=None,
            linked_via=identity_link_service.LINKED_VIA_EXPLICIT,
        )
    )
    session.add(
        AuditLog(
            tenant_id=None,
            user_id=user.id,
            action="register_completed",
            resource_type="user",
            resource_id=user.id,
            ip_address_hash=ip_address_hash,
            event_metadata={"method": "oauth", "provider": AuthProvider.FIREBASE.value},
        )
    )

    tokens = await AuthService(session=session).issue_tokens(
        user, ip_address_hash, role=role
    )
    await session.commit()
    await cache_service.delete(f"user_oauth_reg:{payload.registration_token}")

    if email is not None:
        await messaging_service.send_welcome_email(email, payload.name, account_url)

    logger.info("auth.user_oauth.registered", user_id=str(user.id))
    return tokens


async def _get_user_role(session: AsyncSession) -> Role:
    result = await session.execute(select(Role).where(Role.name == "USER"))
    role = result.scalar_one_or_none()
    if role is None:
        msg = "USER role missing — run scripts/seed_roles.py"
        raise RuntimeError(msg)
    return role
