"""QuickBite — One-time-code sign-in and sign-up for `restaurant.users`.

This is the OTP half of the unified auth surface: one login screen serving
customers, owners, and staff, offering a code, a password, or a social
account as three routes to the same place. The password route is
AuthService.register/login; the social route is user_oauth_service; this
module owns the code route for *user* accounts.

Deliberately separate from customer_otp_service, which does the same job for
`customer.customers`. They look alike and must not be merged: a Customer is
tenant-scoped and identified within one restaurant, a User is global and
carries a role. Sharing an implementation would mean one `tenant_id`
parameter that is required in one direction and meaningless in the other,
and that is precisely the kind of ambiguity an auth path cannot afford.

## The enumeration rule, and where it stops applying

`request_otp` answers identically whether or not the identifier belongs to an
account: it sends a code to a real account, sends nothing to an unknown one,
and returns the same body either way. Walking it therefore reveals nothing —
the AGENTS.md rule ("never return 404 for an unregistered phone") taken to
its conclusion.

`verify_otp` *does* distinguish: an existing account gets a session, an
unknown identifier gets a `registration_required` token. That is not a leak.
Reaching it means holding a code that was only ever delivered to that inbox
or handset, so the only person who learns anything is the person who already
controls the identifier.

The consequence is that a code has to exist for an identifier with no
account. `_pending:` in Redis is that record — an OTP hash bound to an
identifier, with no User row behind it, expiring on its own five minutes
later.

## What is never stored

The plaintext code, anywhere. Redis holds `sha256(code)`, exactly as
customer_otp_service does, so a Redis dump is not a pile of live credentials.
The identifier is the cache-key material as a SHA-256 hash, and travels
AES-256-GCM encrypted inside the pending-registration blob — never in
plaintext in either place.
"""

import json
import uuid

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.core.encryption import decrypt_pii, encrypt_pii, sha256_hex
from app.core.security import (
    generate_otp_code,
    generate_password_reset_token,
    hash_password,
)
from app.db import rls
from app.db.models.audit import AuditLog
from app.db.models.user import Role, User
from app.schemas.auth import (
    CompleteOtpRegistrationRequest,
    MFAChallengeResponse,
    MFAEnrollmentRequiredResponse,
    OtpRegistrationRequiredResponse,
    TokenResponse,
    UserOtpRequestRequest,
    UserOtpSentResponse,
    UserOtpVerifyRequest,
)
from app.services import messaging_service
from app.services.auth_service import AuthService, check_password_strength
from app.services.identity_service import classify_identifier

logger = structlog.get_logger(__name__)

OTP_TTL_SECONDS = 300  # 5 minutes, matching every other OTP window in the app
OTP_RATE_LIMIT_SECONDS = 60
OTP_DAILY_MAX = 10
OTP_MAX_ATTEMPTS = 5
# Window to finish the sign-up form (name, optional password) after the code
# verifies. Same reasoning as customer_otp_service's registration token: long
# enough for a short form, short enough that a leaked token is worthless.
REGISTRATION_TOKEN_TTL_SECONDS = 900


def _normalise(identifier: str, identifier_type: str) -> str:
    """Lowercase an email, leave an E.164 phone alone.

    The hash of this value is the cache key AND the DB lookup key, so request
    and verify have to agree on it exactly or a correct code silently never
    matches.
    """
    identifier = identifier.strip()
    return identifier.lower() if identifier_type == "email" else identifier


async def _find_user(
    session: AsyncSession, identifier_hash: str, identifier_type: str
) -> User | None:
    """Look up a User by email or phone across every tenant.

    `restaurant.users` is RLS-protected and there is no principal yet — the
    whole point of this call is to work out who is asking — so it runs under
    the admin bypass, the same reasoning AuthService._email_exists documents.
    Without it RLS returns zero rows for every caller and every sign-in
    attempt looks like an unknown account.
    """
    column = User.email_hash if identifier_type == "email" else User.phone_hash
    async with rls.admin_bypass_context(session):
        result = await session.execute(select(User).where(column == identifier_hash))
        return result.scalar_one_or_none()


async def request_otp(
    payload: UserOtpRequestRequest, session: AsyncSession
) -> UserOtpSentResponse:
    """Send a sign-in code, or pre-authorise a sign-up, without saying which.

    Both branches write the same `otp:` record and return the same body. The
    only difference is invisible from outside: an unknown identifier also gets
    a `pending:` marker so verify_otp can tell a first-time signup from a
    returning user without a second DB round trip.
    """
    identifier_type = classify_identifier(payload.identifier)
    identifier = _normalise(payload.identifier, identifier_type)
    identifier_hash = sha256_hex(identifier)

    cooldown_key = f"user_otp_cooldown:{identifier_hash}"
    if await cache_service.exists(cooldown_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code": "OTP_RATE_LIMIT",
                    "message": "Please wait before requesting another code.",
                    "retry_after_seconds": OTP_RATE_LIMIT_SECONDS,
                }
            },
            headers={"Retry-After": str(OTP_RATE_LIMIT_SECONDS)},
        )

    daily_key = f"user_otp_daily:{identifier_hash}"
    if await cache_service.incr(daily_key, ttl=86400) > OTP_DAILY_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code": "OTP_DAILY_LIMIT",
                    "message": "Daily limit reached. Please try again tomorrow.",
                }
            },
        )

    await cache_service.set(cooldown_key, "1", ttl=OTP_RATE_LIMIT_SECONDS)

    user = await _find_user(session, identifier_hash, identifier_type)
    otp = generate_otp_code()
    await cache_service.set(
        f"user_otp:{identifier_hash}", sha256_hex(otp), ttl=OTP_TTL_SECONDS
    )
    await cache_service.delete(f"user_otp_attempts:{identifier_hash}")

    if user is None:
        # No account yet. The identifier is kept encrypted, not in plaintext,
        # because this blob outlives the request by five minutes and a Redis
        # dump should not be a list of addresses someone tried to sign up with.
        await cache_service.set(
            f"user_otp_pending:{identifier_hash}",
            json.dumps(
                {
                    "identifier_type": identifier_type,
                    "encrypted_identifier": encrypt_pii(identifier),
                }
            ),
            ttl=OTP_TTL_SECONDS,
        )

    if identifier_type == "phone":
        await messaging_service.send_login_otp_sms(identifier, otp)
        channel = "sms"
    else:
        await messaging_service.send_login_otp_email(
            identifier, otp, OTP_TTL_SECONDS // 60
        )
        channel = "email"

    # `known` never leaves the process — it is here so an operator can see the
    # signup-vs-signin mix in the logs, which the HTTP response deliberately
    # cannot tell them.
    logger.info(
        "auth.user_otp.sent",
        identifier_type=identifier_type,
        identifier_hash=identifier_hash,
        known=user is not None,
    )
    return UserOtpSentResponse(channel=channel, expires_in=OTP_TTL_SECONDS)


async def verify_otp(
    payload: UserOtpVerifyRequest, session: AsyncSession, ip_address_hash: str
) -> (
    MFAChallengeResponse
    | MFAEnrollmentRequiredResponse
    | TokenResponse
    | OtpRegistrationRequiredResponse
):
    """Check the code, then either sign in or hand back a sign-up token.

    An existing account goes through AuthService.complete_authentication, not
    through a private copy of it — an Owner signing in with a code faces the
    same mandatory-MFA gate as one signing in with a password. Skipping that
    here is how a whole authentication factor quietly stops applying to one
    button on the login screen.
    """
    identifier_type = classify_identifier(payload.identifier)
    identifier = _normalise(payload.identifier, identifier_type)
    identifier_hash = sha256_hex(identifier)

    otp_key = f"user_otp:{identifier_hash}"
    stored_hash = await cache_service.get(otp_key)
    if stored_hash is None:
        raise _code_expired_error()

    attempts_key = f"user_otp_attempts:{identifier_hash}"
    if sha256_hex(payload.otp_code) != stored_hash:
        attempts = await cache_service.incr(attempts_key, ttl=OTP_TTL_SECONDS)
        if attempts >= OTP_MAX_ATTEMPTS:
            await cache_service.delete(otp_key)
            await cache_service.delete(attempts_key)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "OTP_TOO_MANY_ATTEMPTS",
                        "message": "Too many attempts. Please request a new code.",
                    }
                },
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "OTP_CODE_INVALID",
                    "message": "Invalid verification code.",
                    "attempts_remaining": OTP_MAX_ATTEMPTS - attempts,
                }
            },
        )

    # Correct code — burn it before doing anything else, so a slow branch
    # below can never be raced with a replay of the same code.
    await cache_service.delete(otp_key)
    await cache_service.delete(attempts_key)

    user = await _find_user(session, identifier_hash, identifier_type)

    if user is None:
        pending_raw = await cache_service.get(f"user_otp_pending:{identifier_hash}")
        if pending_raw is None:
            # The account vanished between request and verify, or the pending
            # marker expired first. Either way there is nothing to sign in to
            # and nothing pre-authorised to register.
            raise _code_expired_error()
        await cache_service.delete(f"user_otp_pending:{identifier_hash}")

        registration_token = generate_password_reset_token()
        await cache_service.set(
            f"user_otp_reg:{registration_token}",
            json.dumps(
                {
                    "identifier_type": identifier_type,
                    "encrypted_identifier": encrypt_pii(identifier),
                    "identifier_hash": identifier_hash,
                }
            ),
            ttl=REGISTRATION_TOKEN_TTL_SECONDS,
        )
        logger.info(
            "auth.user_otp.registration_required",
            identifier_type=identifier_type,
            identifier_hash=identifier_hash,
        )
        return OtpRegistrationRequiredResponse(
            registration_token=registration_token,
            identifier=identifier,
            identifier_type=identifier_type,
            expires_in=REGISTRATION_TOKEN_TTL_SECONDS,
        )

    # Signing in by email code proves the address as thoroughly as clicking a
    # verification link does, so an account that never clicked its link is not
    # left stranded behind the EMAIL_NOT_VERIFIED gate it just satisfied.
    if identifier_type == "email" and not user.email_verified:
        user.email_verified = True

    return await AuthService(session=session).complete_authentication(
        user, ip_address_hash, method="otp"
    )


async def complete_registration(
    payload: CompleteOtpRegistrationRequest,
    session: AsyncSession,
    ip_address_hash: str,
    account_url: str,
) -> TokenResponse:
    """Create the account the verified code pre-authorised, and sign it in.

    The identifier comes from the server-side token, never from the request
    body: a client that could name its own identifier here would be a client
    that could register any address it liked with a code it verified for a
    different one.

    `password` is optional — the plan is "everyone registers as a normal user,
    and may add a password if they prefer one". Omitting it leaves
    hashed_password NULL and the account signs in by code or social only.

    `account_url` is passed in from the route rather than read from config,
    matching how AuthService.register receives its verification link: the
    correct host is whatever host the browser actually reached, which only
    the request knows.
    """
    raw = await cache_service.get(f"user_otp_reg:{payload.registration_token}")
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "REGISTRATION_EXPIRED",
                    "message": "That sign-up expired. Request a new code to start again.",
                }
            },
        )
    pending = json.loads(raw)
    identifier_type = pending["identifier_type"]
    identifier = decrypt_pii(pending["encrypted_identifier"])
    identifier_hash = pending["identifier_hash"]

    password_hash: str | None = None
    if payload.password is not None:
        # Same strength gate the password signup path applies. A weak password
        # is no less weak for having arrived through a different door.
        check_password_strength(payload.password)
        password_hash = hash_password(payload.password)

    # Re-checked after the token was minted: someone may have registered this
    # identifier through another route in the intervening fifteen minutes, and
    # the unique index would otherwise surface it as a 500.
    if await _find_user(session, identifier_hash, identifier_type) is not None:
        await cache_service.delete(f"user_otp_reg:{payload.registration_token}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "ALREADY_REGISTERED",
                    "message": "An account with these details already exists. Try signing in.",
                }
            },
        )

    role = await _get_user_role(session)
    user = User(
        id=uuid.uuid4(),
        tenant_id=None,
        role_id=role.id,
        email_hash=identifier_hash if identifier_type == "email" else None,
        encrypted_email=encrypt_pii(identifier) if identifier_type == "email" else None,
        phone_hash=identifier_hash if identifier_type == "phone" else None,
        encrypted_phone=encrypt_pii(identifier) if identifier_type == "phone" else None,
        hashed_password=password_hash,
        # Born verified: the code they just entered is the proof a verification
        # link would have been asking for. A phone-registered account has no
        # email to verify, and leaving this False would lock it out of
        # complete_authentication's gate forever.
        email_verified=True,
    )
    session.add(user)
    # audit_logs.user_id is a FK onto this row.
    await session.flush()
    session.add(
        AuditLog(
            tenant_id=None,
            user_id=user.id,
            action="register_completed",
            resource_type="user",
            resource_id=user.id,
            ip_address_hash=ip_address_hash,
            event_metadata={"method": "otp", "identifier_type": identifier_type},
        )
    )

    tokens = await AuthService(session=session).issue_tokens(
        user, ip_address_hash, role=role
    )
    await session.commit()
    await cache_service.delete(f"user_otp_reg:{payload.registration_token}")

    await _send_welcome(identifier, identifier_type, payload.name, account_url)

    logger.info(
        "auth.user_otp.registered", user_id=str(user.id), identifier_type=identifier_type
    )
    return tokens


async def _send_welcome(
    identifier: str, identifier_type: str, name: str, account_url: str
) -> None:
    """The "login confirmation" the plan asks for, on whichever channel they
    chose to register with — best-effort, like every other notifier here: a
    mail outage must not undo an account that already exists.
    """
    if identifier_type == "phone":
        await messaging_service.send_welcome_sms(identifier, name)
    else:
        await messaging_service.send_welcome_email(identifier, name, account_url)


async def _get_user_role(session: AsyncSession) -> Role:
    result = await session.execute(select(Role).where(Role.name == "USER"))
    role = result.scalar_one_or_none()
    if role is None:
        msg = "USER role missing — run scripts/seed_roles.py"
        raise RuntimeError(msg)
    return role


def _code_expired_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error": {
                "code": "OTP_CODE_EXPIRED",
                "message": "This code has expired. Please request a new one.",
            }
        },
    )
