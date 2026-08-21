"""QuickBite — Owner/Staff: register, login, mfa, refresh, logout.

AUTH-01 implements registration + email verification. No User or Tenant row
exists until the email is verified: the pending registration lives in Redis
(24h TTL) keyed by the verification token, so unverified accounts can never
log in and abandoned signups expire on their own. Tenant + Owner user are
created in one transaction on verification.

AUTH-02 (login + TOTP MFA) and AUTH-03 (refresh rotation + logout) handle the
second factor. AUTH-04 closes the gap between them and Doc 3's role matrix:
MFA is REQUIRED for Super Admin, Owner, and Manager, so `login()` now consults
`Role.mfa_required` rather than only `User.mfa_enabled`. A user in one of those
roles who has not enrolled gets an `mfa_enrollment_required` challenge instead
of a token pair, and completes enrollment via start/enroll/confirm below.

The `mfa_session:{token}` Redis value carries a `purpose` ("verify" vs
"enroll") alongside the user id. Without it, a session token minted for a
user who already has TOTP could be replayed against /auth/mfa/enroll to
overwrite their secret — turning a stolen half-credential into a full
account takeover.
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from zxcvbn import zxcvbn

from app.core import cache_service
from app.core.config import settings
from app.core.encryption import decrypt_pii, encrypt_pii, sha256_hex
from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_mfa_session_token,
    generate_otp_code,
    generate_password_reset_token,
    generate_totp_secret,
    generate_verification_token,
    hash_password,
    totp_provisioning_uri,
    verify_password_constant_time,
    verify_totp_code,
)
from app.db import bootstrap, rls
from app.db.models.audit import AuditLog
from app.db.models.static_data import BusinessCategory
from app.db.models.subscription import SubscriptionPlan
from app.db.models.tenant import Tenant
from app.db.models.user import Role, Session, User
from app.schemas.auth import (
    BecomeRestaurantRequest,
    BecomeRestaurantResponse,
    ContactOtpRequestRequest,
    ContactOtpRequestResponse,
    ContactOtpVerifyRequest,
    ContactOtpVerifyResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    MeResponse,
    MFAChallengeResponse,
    MFAConfirmRequest,
    MFAEnrollmentRequiredResponse,
    MFAEnrollRequest,
    MFAEnrollResponse,
    MFAStartResponse,
    MFAVerify,
    RefreshRequest,
    RegisterResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    StatusResponse,
    TenantLookupResponse,
    TokenResponse,
    UserLogin,
    UserRegister,
    VerifyEmailResponse,
)
from app.services import messaging_service
from app.services.identity_service import classify_identifier, classify_staff_identifier

logger = structlog.get_logger(__name__)

PENDING_REGISTRATION_TTL_SECONDS = 86400  # verification link valid 24h
PASSWORD_RESET_TTL_SECONDS = 1800  # 30 min — a bearer credential against an
# existing account, so its window is much tighter than the verification token's
MIN_ZXCVBN_SCORE = 3

MFA_SESSION_TTL_SECONDS = 300  # 5 minutes, per Doc 3
MFA_MAX_ATTEMPTS = 3
MFA_CODE_REPLAY_WINDOW_SECONDS = 90  # blocks reusing a code across two 30s TOTP steps
FAILED_LOGIN_LOCKOUT_THRESHOLD = 10
ACCOUNT_LOCKOUT_MINUTES = 60

# --- "Join Us": second-contact verification for become_restaurant() -------
# Which contact gets verified depends on how the account was created: a user
# who signed up with an email verifies a phone here, one who signed up with a
# phone verifies an email. Same window and same limits either way — the
# channel changes, the security properties do not.
CONTACT_OTP_TTL_SECONDS = 300  # 5 minutes, same window as customer OTP
CONTACT_OTP_RATE_LIMIT_SECONDS = 60
CONTACT_OTP_MAX_ATTEMPTS = 3
# Window to finish "Join Us" (name + category + plan + submit) after the OTP
# verifies — mirrors REGISTRATION_TOKEN_TTL_SECONDS's reasoning in
# customer_otp_service: long enough to fill in a short form, short enough
# that a leaked token is worthless within the hour.
CONTACT_VERIFICATION_TOKEN_TTL_SECONDS = 900


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def check_password_strength(password: str) -> None:
    """Raise 422 unless zxcvbn scores the password at least MIN_ZXCVBN_SCORE.

    Module-level so the team-invite flow applies the identical bar to a Manager's
    or Staff member's first password as registration does to an Owner's — a weak
    Manager password is the same foothold into the tenant.
    """
    result = zxcvbn(password)
    if result["score"] < MIN_ZXCVBN_SCORE:
        suggestions = result["feedback"]["suggestions"] or [
            "Use a longer passphrase with unrelated words."
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "PASSWORD_TOO_WEAK",
                    "message": "Please choose a stronger password.",
                    "suggestions": suggestions,
                }
            },
        )


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def register(self, request: UserRegister, verification_base_url: str) -> RegisterResponse:
        self._check_password_strength(request.password)

        # Hash before the duplicate lookup so both outcomes cost one bcrypt
        # round — no timing oracle on whether the email is registered.
        password_hash = hash_password(request.password)
        email = request.email.lower()
        email_hash = sha256_hex(email)

        if await self._email_exists(email_hash):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "EMAIL_ALREADY_REGISTERED",
                        "message": "An account with this email already exists. Try logging in.",
                    }
                },
            )

        username = request.username.lower() if request.username else None
        if username and await self._username_exists(sha256_hex(username)):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "USERNAME_ALREADY_TAKEN",
                        "message": "That username is already taken.",
                    }
                },
            )

        token = generate_verification_token()
        await cache_service.set(
            f"pending_reg:{token}",
            json.dumps(
                {
                    "email": email,
                    "password_hash": password_hash,
                    "name": request.name,
                    "username": username,
                }
            ),
            ttl=PENDING_REGISTRATION_TTL_SECONDS,
        )

        email_sent = await messaging_service.send_verification_email(
            email, f"{verification_base_url}?token={token}"
        )

        self.session.add(
            AuditLog(
                action="register_requested",
                resource_type="user",
                event_metadata={"email_hash": email_hash, "email_sent": email_sent},
            )
        )
        await self.session.commit()

        logger.info("auth.register.requested", email_hash=email_hash, email_sent=email_sent)
        return RegisterResponse(status="verification_email_sent")

    async def lookup_tenant(self, subdomain: str) -> TenantLookupResponse:
        """Resolves a restaurant's subdomain to its tenant_id for the
        identify-first login screen (pre-TENANT-01: no Host-header routing
        yet, so the client has to ask). `restaurant.tenants` carries no RLS
        policy — it is the tenant registry, not a tenant-scoped table — so
        this plain lookup is safe with no tenant context set."""
        slug = subdomain.strip().lower()
        result = await self.session.execute(
            select(Tenant).where(Tenant.subdomain == slug, Tenant.is_active.is_(True))
        )
        tenant = result.scalar_one_or_none()
        if tenant is None:
            return TenantLookupResponse(found=False)
        return TenantLookupResponse(found=True, tenant_id=str(tenant.id), name=tenant.name)

    async def verify_email(self, token: str) -> VerifyEmailResponse:
        raw = await cache_service.get(f"pending_reg:{token}")
        if raw is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "VERIFICATION_TOKEN_INVALID",
                        "message": "This verification link is invalid or has expired.",
                    }
                },
            )
        pending = json.loads(raw)
        email_hash = sha256_hex(pending["email"])

        # The email may have been registered between request and click.
        if await self._email_exists(email_hash):
            await cache_service.delete(f"pending_reg:{token}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "EMAIL_ALREADY_REGISTERED",
                        "message": "An account with this email already exists. Try logging in.",
                    }
                },
            )

        username = pending.get("username")
        username_hash = sha256_hex(username) if username else None
        if username_hash and await self._username_exists(username_hash):
            await cache_service.delete(f"pending_reg:{token}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "USERNAME_ALREADY_TAKEN",
                        "message": "That username is already taken.",
                    }
                },
            )

        user_role = await self._get_user_role()

        # Standard, tenant-less account — no Tenant exists until the caller
        # later registers a restaurant (become_restaurant()). users' RLS
        # policy admits tenant_id IS NULL rows unconditionally (migration
        # 0012), so this INSERT needs no tenant context bound.
        user = User(
            id=uuid.uuid4(),
            tenant_id=None,
            role_id=user_role.id,
            email_hash=email_hash,
            encrypted_email=encrypt_pii(pending["email"]),
            username_hash=username_hash,
            encrypted_username=encrypt_pii(username) if username else None,
            hashed_password=pending["password_hash"],
            email_verified=True,
        )
        self.session.add(user)
        # audit_logs.user_id is a FK onto this row.
        await self.session.flush()
        self.session.add(
            AuditLog(
                tenant_id=None,
                user_id=user.id,
                action="email_verified",
                resource_type="user",
                resource_id=user.id,
            )
        )
        await self.session.commit()
        await cache_service.delete(f"pending_reg:{token}")

        logger.info("auth.register.verified", user_id=str(user.id))
        return VerifyEmailResponse(status="verified", subdomain=None)

    async def request_contact_otp(
        self, user: User, request: ContactOtpRequestRequest
    ) -> ContactOtpRequestResponse:
        """"Join Us" step: send an OTP to the second contact method the caller
        is registering their business with.

        The contact's own shape decides the channel — the client never says
        which it sent, because a client that names the channel is a client
        that can ask for an SMS to be dispatched to an email address.

        Rate-limited per user, not per contact: a caller re-typing a mistyped
        number should not get a fresh cooldown window for free.
        """
        contact_type = classify_identifier(request.contact)
        contact = self._normalise_contact(request.contact, contact_type)

        cooldown_key = f"contact_otp_cooldown:{user.id}"
        if await cache_service.exists(cooldown_key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": {
                        "code": "OTP_RATE_LIMIT",
                        "message": "Please wait before requesting another code.",
                        "retry_after_seconds": CONTACT_OTP_RATE_LIMIT_SECONDS,
                    }
                },
                headers={"Retry-After": str(CONTACT_OTP_RATE_LIMIT_SECONDS)},
            )

        contact_hash = sha256_hex(contact)
        otp = generate_otp_code()
        await cache_service.set(
            f"contact_otp:{user.id}:{contact_hash}", sha256_hex(otp), ttl=CONTACT_OTP_TTL_SECONDS
        )
        await cache_service.set(cooldown_key, "1", ttl=CONTACT_OTP_RATE_LIMIT_SECONDS)

        if contact_type == "phone":
            await messaging_service.send_business_phone_otp_sms(contact, otp)
            channel = "sms"
        else:
            await messaging_service.send_business_email_otp(contact, otp)
            channel = "email"

        logger.info("auth.contact_otp.sent", user_id=str(user.id), contact_type=contact_type)
        return ContactOtpRequestResponse(status="otp_sent", channel=channel)

    async def verify_contact_otp(
        self, user: User, request: ContactOtpVerifyRequest
    ) -> ContactOtpVerifyResponse:
        contact_type = classify_identifier(request.contact)
        contact = self._normalise_contact(request.contact, contact_type)
        contact_hash = sha256_hex(contact)
        otp_key = f"contact_otp:{user.id}:{contact_hash}"
        stored_hash = await cache_service.get(otp_key)
        if stored_hash is None:
            raise self._contact_otp_code_expired_error()

        attempts_key = f"contact_otp_attempts:{user.id}:{contact_hash}"
        if sha256_hex(request.otp_code) != stored_hash:
            attempts = await cache_service.incr(attempts_key, ttl=CONTACT_OTP_TTL_SECONDS)
            if attempts >= CONTACT_OTP_MAX_ATTEMPTS:
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
                        "attempts_remaining": CONTACT_OTP_MAX_ATTEMPTS - attempts,
                    }
                },
            )

        await cache_service.delete(otp_key)
        await cache_service.delete(attempts_key)

        token = generate_password_reset_token()  # same shape: opaque, single-use, url-safe
        await cache_service.set(
            f"contact_verify_token:{token}",
            json.dumps(
                {
                    "user_id": str(user.id),
                    "contact_type": contact_type,
                    "contact_hash": contact_hash,
                    "encrypted_contact": encrypt_pii(contact),
                }
            ),
            ttl=CONTACT_VERIFICATION_TOKEN_TTL_SECONDS,
        )

        logger.info("auth.contact_otp.verified", user_id=str(user.id), contact_type=contact_type)
        return ContactOtpVerifyResponse(
            contact_verification_token=token,
            expires_in=CONTACT_VERIFICATION_TOKEN_TTL_SECONDS,
        )

    @staticmethod
    def _normalise_contact(contact: str, contact_type: str) -> str:
        """Lowercase an email, leave an E.164 phone byte-for-byte alone.

        The hash is the lookup key, so "Owner@Cafe.in" and "owner@cafe.in" have
        to agree between the request and the verify call or the code silently
        never matches. Phone numbers are already canonical by the time
        classify_identifier accepts them.
        """
        return contact.strip().lower() if contact_type == "email" else contact.strip()

    async def _resolve_contact_verification(self, user: User, token: str) -> dict:
        """Redeem a contact_verification_token minted by verify_contact_otp().

        Single-use and scoped to the user who verified it — a token cannot be
        replayed by a different account, and cannot be reused across two
        become_restaurant() attempts."""
        raw = await cache_service.get(f"contact_verify_token:{token}")
        if raw is None:
            raise self._contact_verification_expired_error()
        data = json.loads(raw)
        if data.get("user_id") != str(user.id):
            raise self._contact_verification_expired_error()
        await cache_service.delete(f"contact_verify_token:{token}")
        return data

    @staticmethod
    def _contact_otp_code_expired_error() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "OTP_CODE_EXPIRED",
                    "message": "This code has expired. Please request a new one.",
                }
            },
        )

    @staticmethod
    def _contact_verification_expired_error() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "CONTACT_VERIFICATION_EXPIRED",
                    "message": "That verification expired. Please request a new code.",
                }
            },
        )

    async def become_restaurant(
        self, user: User, payload: BecomeRestaurantRequest, ip_address_hash: str
    ) -> MFAEnrollmentRequiredResponse | BecomeRestaurantResponse:
        """A standard user's one-time upgrade to Owner: creates their Tenant,
        promotes their role, revokes the now-stale tenant-less token, and
        issues a fresh one — subject to the same MFA-required-for-Owner rule
        login() enforces, since this mints a token pair too."""
        if user.tenant_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "ALREADY_HAS_RESTAURANT",
                        "message": "This account is already linked to a restaurant.",
                    }
                },
            )

        # Redeemed before any Tenant is created: an expired/foreign token
        # fails the request with nothing to roll back.
        contact_data = await self._resolve_contact_verification(
            user, payload.contact_verification_token
        )

        category = await self._get_active_category(payload.category_id)
        plan = await self._get_active_plan(payload.plan_id)
        owner_role = await self._get_owner_role()
        subdomain = await self._unique_subdomain(payload.restaurant_name)

        tenant = Tenant(
            id=uuid.uuid4(),
            subdomain=subdomain,
            name=payload.restaurant_name,
            category_id=category.id,
            plan_id=plan.id,
            onboarding_state="email_verified",
            is_active=True,
        )
        # Same reasoning as the old verify_email(): restaurant.tenants itself
        # carries no RLS policy, but users/audit_logs do, and the writes below
        # carry tenant.id in that column — nothing is visible under RLS until
        # context is bound to the tenant that owns it.
        await rls.set_tenant_context(self.session, tenant.id)
        self.session.add(tenant)
        # Forces the tenant INSERT to run now, so the User UPDATE and AuditLog
        # INSERT below reference a tenant.id Postgres can already see.
        await self.session.flush()

        user.tenant_id = tenant.id
        user.role_id = owner_role.id
        # The verified second contact fills whichever field the account was
        # missing. An email-registered user arrives here having verified a
        # phone; a phone-registered user having verified an email. Either way
        # the Owner ends up reachable on both channels, which is what the join
        # confirmation, lockout warnings, and reward alerts all assume.
        if contact_data["contact_type"] == "phone":
            user.phone_hash = contact_data["contact_hash"]
            user.encrypted_phone = contact_data["encrypted_contact"]
        else:
            user.email_hash = contact_data["contact_hash"]
            user.encrypted_email = contact_data["encrypted_contact"]

        self.session.add(
            AuditLog(
                tenant_id=tenant.id,
                user_id=user.id,
                action="upgraded_to_owner",
                resource_type="tenant",
                resource_id=tenant.id,
                ip_address_hash=ip_address_hash,
                event_metadata={"plan_id": str(plan.id), "category": category.slug},
            )
        )

        # The token that authenticated this request was minted for a
        # tenant-less USER — it's stale the instant this commits (wrong role,
        # no tenant to scope). Same two calls logout_all() uses.
        await self._bump_tokens_valid_from(user.id)
        await self._revoke_all_sessions(user.id)

        # Doc 3: MFA is REQUIRED for Owner. Mirror login()'s branch rather
        # than unconditionally issuing tokens — an upgraded account with no
        # TOTP enrolled must not walk away with a full session.
        if owner_role.mfa_required and not user.mfa_enabled:
            mfa_token = await self._issue_mfa_session(user, purpose="enroll")
            await self.session.commit()
            await self._send_join_success_notifications(user, payload.restaurant_name)
            logger.info(
                "auth.become_restaurant.mfa_enrollment_required",
                tenant_id=str(tenant.id),
                user_id=str(user.id),
            )
            return MFAEnrollmentRequiredResponse(
                status="mfa_enrollment_required",
                mfa_session_token=mfa_token,
                expires_in=MFA_SESSION_TTL_SECONDS,
                role=owner_role.name,
            )

        tokens = await self.issue_tokens(user, ip_address_hash, role=owner_role)
        await self.session.commit()
        await self._send_join_success_notifications(user, payload.restaurant_name)
        logger.info(
            "auth.become_restaurant.success", tenant_id=str(tenant.id), user_id=str(user.id)
        )
        return BecomeRestaurantResponse(
            status="restaurant_created",
            tenant_id=str(tenant.id),
            subdomain=subdomain,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.expires_in,
            role=owner_role.name,
        )

    async def forgot_password(
        self, request: ForgotPasswordRequest, reset_base_url: str
    ) -> ForgotPasswordResponse:
        """Always returns the same response whether or not the identifier
        matched an account — a differentiated response here would let an
        attacker map out registered emails/usernames one guess at a time,
        the same enumeration concern AGENTS.md flags for the customer OTP
        flow. Only a real match writes an audit row or sends an email, so
        the audit trail itself is not an oracle either."""
        identifier_type = classify_staff_identifier(request.identifier)
        identifier_hash = sha256_hex(request.identifier.lower())
        user = await self._get_user_by_identifier_hash(identifier_type, identifier_hash)

        if user is not None and user.is_active:
            token = generate_password_reset_token()
            await cache_service.set(
                f"pending_reset:{token}",
                json.dumps(
                    {
                        "user_id": str(user.id),
                        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
                    }
                ),
                ttl=PASSWORD_RESET_TTL_SECONDS,
            )
            await messaging_service.send_password_reset_email(
                decrypt_pii(user.encrypted_email), f"{reset_base_url}?token={token}"
            )
            self.session.add(
                AuditLog(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    action="password_reset_requested",
                    resource_type="user",
                    resource_id=user.id,
                    event_metadata={"identifier_hash": identifier_hash},
                )
            )
            await self.session.commit()

        logger.info("auth.forgot_password.requested", identifier_hash=identifier_hash)
        return ForgotPasswordResponse(status="if_registered_email_sent")

    async def reset_password(
        self, request: ResetPasswordRequest, ip_address_hash: str
    ) -> ResetPasswordResponse:
        raw = await cache_service.get(f"pending_reset:{request.token}")
        if raw is None:
            raise self._reset_token_invalid_error()
        pending = json.loads(raw)
        user_id = uuid.UUID(pending["user_id"])
        tenant_id = uuid.UUID(pending["tenant_id"]) if pending.get("tenant_id") else None

        self._check_password_strength(request.new_password)

        user = await self._get_user_for_reset(user_id, tenant_id)
        if user is None or not user.is_active:
            await cache_service.delete(f"pending_reset:{request.token}")
            raise self._reset_token_invalid_error()

        user.hashed_password = hash_password(request.new_password)
        user.failed_login_count = 0
        user.locked_until = None

        # Every existing session must die the moment the password changes —
        # same two calls logout_all()/become_restaurant() already use.
        await self._bump_tokens_valid_from(user.id)
        await self._revoke_all_sessions(user.id)

        self.session.add(
            AuditLog(
                tenant_id=user.tenant_id,
                user_id=user.id,
                action="password_reset_completed",
                resource_type="user",
                resource_id=user.id,
                ip_address_hash=ip_address_hash,
            )
        )
        await self.session.commit()
        await cache_service.delete(f"pending_reset:{request.token}")  # single-use

        await messaging_service.send_password_changed_email(decrypt_pii(user.encrypted_email))

        logger.info("auth.reset_password.completed", user_id=str(user.id))
        return ResetPasswordResponse(status="password_reset")

    async def _send_join_success_notifications(self, user: User, restaurant_name: str) -> None:
        """Best-effort — a mail/SMS outage must never fail an already-committed
        restaurant registration. Fires on both become_restaurant() branches:
        the Tenant exists the moment this is called, whether or not MFA
        enrollment is still pending.

        Both channels are guarded rather than assumed. By the time this runs
        the Owner normally has an email *and* a phone (one from registration,
        one from the contact-OTP step), but a partially-populated account must
        degrade to sending on whichever channel exists, not raise inside a
        best-effort notifier and swallow the other one with it."""
        if user.encrypted_email:
            await messaging_service.send_restaurant_joined_email(
                decrypt_pii(user.encrypted_email), restaurant_name
            )
        if user.encrypted_phone:
            await messaging_service.send_restaurant_joined_sms(
                decrypt_pii(user.encrypted_phone), restaurant_name
            )

    def _check_password_strength(self, password: str) -> None:
        check_password_strength(password)

    async def _email_exists(self, email_hash: str) -> bool:
        """Email must be globally unique across tenants (`users.email_hash` has
        a DB-level unique constraint), but `users` is RLS-protected and neither
        caller has a tenant context yet — register() has no principal at all,
        and verify_email()'s new tenant doesn't exist until after this check.
        Without the bypass, RLS silently returns zero rows for every caller
        (see rls.py's module docstring), this check always says "not taken",
        and the real conflict only surfaces as an unhandled IntegrityError
        when the INSERT hits the unique constraint."""
        async with rls.admin_bypass_context(self.session):
            result = await self.session.execute(
                select(User.id).where(User.email_hash == email_hash)
            )
            return result.scalar_one_or_none() is not None

    async def _username_exists(self, username_hash: str) -> bool:
        """Same cross-tenant-uniqueness reasoning as `_email_exists` above."""
        async with rls.admin_bypass_context(self.session):
            result = await self.session.execute(
                select(User.id).where(User.username_hash == username_hash)
            )
            return result.scalar_one_or_none() is not None

    async def _get_owner_role(self) -> Role:
        result = await self.session.execute(select(Role).where(Role.name == "OWNER"))
        role = result.scalar_one_or_none()
        if role is None:
            msg = "OWNER role missing — run scripts/seed_roles.py"
            raise RuntimeError(msg)
        return role

    async def _get_user_role(self) -> Role:
        result = await self.session.execute(select(Role).where(Role.name == "USER"))
        role = result.scalar_one_or_none()
        if role is None:
            msg = "USER role missing — run scripts/seed_roles.py"
            raise RuntimeError(msg)
        return role

    async def _get_active_category(self, category_id: uuid.UUID) -> BusinessCategory:
        """The business category chosen one step before the plan.

        Validated here rather than trusted from the client because the plan
        list the caller picked from was filtered by it: a mismatched pair
        would record a tenant on a plan its own category never offered, and
        nothing downstream would ever notice.
        """
        result = await self.session.execute(
            select(BusinessCategory).where(
                BusinessCategory.id == category_id, BusinessCategory.is_active.is_(True)
            )
        )
        category = result.scalar_one_or_none()
        if category is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "CATEGORY_NOT_FOUND",
                        "message": "Pick a business category to continue.",
                    }
                },
            )
        return category

    async def _get_active_plan(self, plan_id: uuid.UUID) -> SubscriptionPlan:
        """Existence + is_active only — provider provisioning
        (`provider_plan_id`) is validated separately by
        BillingService.create_checkout_order when the caller follows up with
        POST /billing/checkout using their new OWNER token."""
        result = await self.session.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.id == plan_id, SubscriptionPlan.is_active.is_(True)
            )
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {"code": "PLAN_NOT_FOUND", "message": "No such subscription plan."}
                },
            )
        return plan

    async def _unique_subdomain(self, restaurant_name: str) -> str:
        base = re.sub(r"[^a-z0-9-]", "", restaurant_name.lower().replace(" ", "-")).strip("-")
        base = base[:40] or "restaurant"
        candidate = base
        suffix = 1
        while True:
            result = await self.session.execute(
                select(Tenant.id).where(Tenant.subdomain == candidate)
            )
            if result.scalar_one_or_none() is None:
                return candidate
            suffix += 1
            candidate = f"{base}-{suffix}"

    # --- AUTH-02: login + TOTP MFA -------------------------------------

    async def login(
        self, request: UserLogin, ip_address_hash: str
    ) -> MFAChallengeResponse | MFAEnrollmentRequiredResponse | TokenResponse:
        identifier_type = classify_staff_identifier(request.identifier)
        identifier_hash = sha256_hex(request.identifier.lower())
        user = await self._get_user_by_identifier_hash(identifier_type, identifier_hash)

        # Always pay the bcrypt cost, even for an unknown identifier — no timing
        # oracle that reveals whether the account is registered.
        password_ok = verify_password_constant_time(
            request.password, user.hashed_password if user else None
        )

        if user is None:
            logger.info(
                "auth.login.unknown_identifier",
                identifier_type=identifier_type,
                identifier_hash=identifier_hash,
            )
            raise self._invalid_credentials_error()

        if user.locked_until is not None and user.locked_until > _utcnow():
            raise self._account_locked_error()

        if not password_ok:
            # An account created by OTP or social sign-in has no password to
            # verify (hashed_password NULL, migration 0014). Saying so is not
            # an enumeration leak — the caller already typed a correct
            # identifier — and "invalid credentials" would send them round the
            # same dead end forever with the right answer one tab away.
            if user.hashed_password is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": "PASSWORD_NOT_SET",
                            "message": (
                                "This account signs in with a code or a social "
                                "account. Use one of those, then add a password "
                                "from your profile if you want one."
                            ),
                        }
                    },
                )
            await self._register_failed_login(user, ip_address_hash)
            if user.locked_until is not None and user.locked_until > _utcnow():
                raise self._account_locked_error()
            raise self._invalid_credentials_error()

        return await self.complete_authentication(user, ip_address_hash, method="password")

    async def complete_authentication(
        self, user: User, ip_address_hash: str, *, method: str
    ) -> MFAChallengeResponse | MFAEnrollmentRequiredResponse | TokenResponse:
        """Everything that happens once an identity is proven, whatever proved it.

        Password, OTP, and social sign-in all converge here, which is the point:
        Doc 3's "MFA REQUIRED for Super Admin, Owner, and Manager" has to hold
        no matter which button the user pressed. A second copy of this branch
        living in the OTP path is exactly how an Owner ends up with
        single-factor access through the door nobody re-read.

        `method` is a structlog label only — it never changes what is enforced.
        """
        # Checked only after the credential verifies, so it cannot be used to
        # enumerate which accounts an Owner has deactivated.
        if not user.is_active:
            raise self._account_deactivated_error()

        # Accounts created by OTP or social sign-in are born verified — the
        # identifier they proved control of *is* the verification — so this
        # only ever blocks a password signup that never clicked its link.
        if not user.email_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "EMAIL_NOT_VERIFIED",
                        "message": "Please verify your email. Check your inbox.",
                    }
                },
            )

        user.failed_login_count = 0
        role = await self._get_role_by_id(user.role_id)

        if user.mfa_enabled:
            token = await self._issue_mfa_session(user, purpose="verify")
            await self.session.commit()
            logger.info(
                "auth.login.first_factor_verified",
                user_id=str(user.id),
                role=role.name,
                method=method,
            )
            return MFAChallengeResponse(
                status="mfa_required",
                mfa_session_token=token,
                expires_in=MFA_SESSION_TTL_SECONDS,
            )

        # Doc 3: MFA is REQUIRED for Super Admin, Owner, and Manager. Issuing a
        # token pair here would hand a privileged account single-factor access,
        # so they get a limited enrollment session instead.
        if role.mfa_required:
            token = await self._issue_mfa_session(user, purpose="enroll")
            self.session.add(
                AuditLog(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    action="mfa_enrollment_required",
                    resource_type="user",
                    resource_id=user.id,
                    ip_address_hash=ip_address_hash,
                    event_metadata={"role": role.name, "method": method},
                )
            )
            await self.session.commit()
            logger.info(
                "auth.login.mfa_enrollment_required",
                user_id=str(user.id),
                role=role.name,
                method=method,
            )
            return MFAEnrollmentRequiredResponse(
                status="mfa_enrollment_required",
                mfa_session_token=token,
                expires_in=MFA_SESSION_TTL_SECONDS,
                role=role.name,
            )

        tokens = await self.issue_tokens(user, ip_address_hash, role=role)
        self._log_login_success(user, ip_address_hash)
        await self.session.commit()
        logger.info("auth.login.success", user_id=str(user.id), role=role.name, method=method)
        return tokens

    async def verify_mfa(self, request: MFAVerify, ip_address_hash: str) -> TokenResponse:
        user = await self._resolve_mfa_session(request.mfa_session_token, expected_purpose="verify")

        replay_key = f"mfa_used:{user.id}:{request.totp_code}"
        if await cache_service.exists(replay_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "MFA_CODE_ALREADY_USED",
                        "message": "That code was already used. Wait for the next one.",
                    }
                },
            )

        totp_secret = decrypt_pii(user.totp_secret) if user.totp_secret else None
        if totp_secret is None or not verify_totp_code(totp_secret, request.totp_code):
            fail_key = f"mfa_fail:{request.mfa_session_token}"
            fail_count = await cache_service.incr(fail_key, ttl=MFA_SESSION_TTL_SECONDS)
            if fail_count >= MFA_MAX_ATTEMPTS:
                await cache_service.delete(f"mfa_session:{request.mfa_session_token}")
                await cache_service.delete(fail_key)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error": {
                            "code": "MFA_TOO_MANY_ATTEMPTS",
                            "message": "Too many attempts. Please log in again.",
                        }
                    },
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "MFA_CODE_INVALID",
                        "message": "Invalid verification code.",
                        "attempts_remaining": MFA_MAX_ATTEMPTS - fail_count,
                    }
                },
            )

        await cache_service.set(replay_key, "1", ttl=MFA_CODE_REPLAY_WINDOW_SECONDS)
        await cache_service.delete(f"mfa_session:{request.mfa_session_token}")
        await cache_service.delete(f"mfa_fail:{request.mfa_session_token}")

        tokens = await self.issue_tokens(user, ip_address_hash)
        self._log_login_success(user, ip_address_hash)
        await self.session.commit()
        logger.info("auth.mfa.verified", user_id=str(user.id))
        return tokens

    # --- AUTH-04: TOTP enrollment ---------------------------------------

    async def start_mfa_enrollment(self, user: User) -> MFAStartResponse:
        """Voluntary enrollment for an already-authenticated user.

        The forced path (a role with mfa_required and no secret) gets its
        enroll-purpose session straight from login(); this is the opt-in path
        for roles where Doc 3 makes MFA optional, e.g. Staff.
        """
        token = await self._issue_mfa_session(user, purpose="enroll")
        logger.info("auth.mfa.enrollment_started", user_id=str(user.id))
        return MFAStartResponse(mfa_session_token=token, expires_in=MFA_SESSION_TTL_SECONDS)

    async def enroll_mfa(self, request: MFAEnrollRequest) -> MFAEnrollResponse:
        """Mint a candidate TOTP secret and park it in Redis, not on the User row.

        Writing `user.totp_secret` here would enable MFA for an authenticator
        that may never have scanned the QR — locking the user out of their own
        account. The secret only lands in the DB once confirm_mfa() sees a
        working code derived from it.
        """
        user = await self._resolve_mfa_session(request.mfa_session_token, expected_purpose="enroll")

        secret = generate_totp_secret()
        await cache_service.set(
            f"mfa_enroll_secret:{request.mfa_session_token}",
            encrypt_pii(secret),
            ttl=MFA_SESSION_TTL_SECONDS,
        )

        logger.info("auth.mfa.secret_issued", user_id=str(user.id))
        return MFAEnrollResponse(
            secret=secret,
            provisioning_uri=totp_provisioning_uri(secret, decrypt_pii(user.encrypted_email)),
            expires_in=MFA_SESSION_TTL_SECONDS,
        )

    async def confirm_mfa(self, request: MFAConfirmRequest, ip_address_hash: str) -> TokenResponse:
        """Prove the authenticator holds the candidate secret, then persist it.

        Returns a full token pair: for the forced path this is the step that
        finally completes the login the enrollment challenge interrupted.
        """
        user = await self._resolve_mfa_session(request.mfa_session_token, expected_purpose="enroll")

        secret_key = f"mfa_enroll_secret:{request.mfa_session_token}"
        encrypted_secret = await cache_service.get(secret_key)
        if encrypted_secret is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "MFA_ENROLLMENT_NOT_STARTED",
                        "message": "Start setup again to get a fresh QR code.",
                    }
                },
            )

        secret = decrypt_pii(encrypted_secret)
        if not verify_totp_code(secret, request.totp_code):
            fail_key = f"mfa_fail:{request.mfa_session_token}"
            fail_count = await cache_service.incr(fail_key, ttl=MFA_SESSION_TTL_SECONDS)
            if fail_count >= MFA_MAX_ATTEMPTS:
                await cache_service.delete(f"mfa_session:{request.mfa_session_token}")
                await cache_service.delete(secret_key)
                await cache_service.delete(fail_key)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error": {
                            "code": "MFA_TOO_MANY_ATTEMPTS",
                            "message": "Too many attempts. Please log in again.",
                        }
                    },
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "MFA_CODE_INVALID",
                        "message": "Invalid verification code.",
                        "attempts_remaining": MFA_MAX_ATTEMPTS - fail_count,
                    }
                },
            )

        user.totp_secret = encrypt_pii(secret)
        user.mfa_enabled = True

        await cache_service.set(
            f"mfa_used:{user.id}:{request.totp_code}", "1", ttl=MFA_CODE_REPLAY_WINDOW_SECONDS
        )
        await cache_service.delete(f"mfa_session:{request.mfa_session_token}")
        await cache_service.delete(secret_key)
        await cache_service.delete(f"mfa_fail:{request.mfa_session_token}")

        self.session.add(
            AuditLog(
                tenant_id=user.tenant_id,
                user_id=user.id,
                action="mfa_enrolled",
                resource_type="user",
                resource_id=user.id,
                ip_address_hash=ip_address_hash,
            )
        )
        tokens = await self.issue_tokens(user, ip_address_hash)
        self._log_login_success(user, ip_address_hash)
        await self.session.commit()
        logger.info("auth.mfa.enrolled", user_id=str(user.id))
        return tokens

    # --- AUTH-04: current-user identity ---------------------------------

    async def me(self, user: User) -> MeResponse:
        role = await self._get_role_by_id(user.role_id)
        return MeResponse(
            user_id=str(user.id),
            tenant_id=str(user.tenant_id) if user.tenant_id else None,
            email=decrypt_pii(user.encrypted_email) if user.encrypted_email else None,
            phone=decrypt_pii(user.encrypted_phone) if user.encrypted_phone else None,
            username=decrypt_pii(user.encrypted_username) if user.encrypted_username else None,
            role=role.name,
            role_level=role.level,
            permissions=role.permissions or {},
            mfa_enabled=user.mfa_enabled,
            mfa_required=role.mfa_required,
            email_verified=user.email_verified,
        )

    # --- AUTH-03: refresh rotation + logout -----------------------------

    async def refresh(self, request: RefreshRequest, ip_address_hash: str, user_agent: str) -> TokenResponse:
        token_hash = sha256_hex(request.refresh_token)
        existing = await self._get_session_by_token_hash(token_hash)

        if existing is None:
            raise self._refresh_invalid_error()

        if existing.revoked:
            # Single-use token replayed after rotation — treat as compromise.
            await self._revoke_all_sessions(existing.user_id)
            self.session.add(
                AuditLog(
                    tenant_id=existing.tenant_id,
                    user_id=existing.user_id,
                    action="refresh_token_replay_detected",
                    resource_type="session",
                    resource_id=existing.id,
                    ip_address_hash=ip_address_hash,
                )
            )
            await self.session.commit()
            logger.warning("auth.refresh.replay_detected", user_id=str(existing.user_id))
            raise self._refresh_invalid_error()

        if existing.expires_at <= _utcnow():
            raise self._refresh_invalid_error()

        existing.revoked = True
        user = await self._get_user_by_id(existing.user_id)
        if user is None:
            raise self._refresh_invalid_error()

        tokens = await self.issue_tokens(user, ip_address_hash, user_agent)
        await self.session.commit()
        logger.info("auth.refresh.rotated", user_id=str(user.id))
        return tokens

    async def logout(self, jti: str, exp: datetime, refresh_token: str) -> StatusResponse:
        ttl = int((exp - _utcnow()).total_seconds())
        if ttl > 0:
            await cache_service.set(f"revoked_jti:{jti}", "1", ttl=ttl)

        existing = await self._get_session_by_token_hash(sha256_hex(refresh_token))
        if existing is not None:
            existing.revoked = True
            # Also revokes any Supabase/Firebase session linked to this user.
            # Those providers expose no server-side per-session handle, so a
            # local logout intentionally logs the account out everywhere —
            # there is no narrower revocation available for them.
            await self._bump_tokens_valid_from(existing.user_id)
        await self.session.commit()
        return StatusResponse(status="logged_out")

    async def logout_all(self, user_id: uuid.UUID) -> StatusResponse:
        await self._revoke_all_sessions(user_id)
        await self._bump_tokens_valid_from(user_id)
        await self.session.commit()
        return StatusResponse(status="logged_out")

    # --- Shared helpers --------------------------------------------------

    async def _issue_mfa_session(self, user: User, purpose: str) -> str:
        """Mint a short-lived, single-purpose credential for the MFA step.

        `purpose` pins the token to one flow. A "verify" token cannot drive
        enrollment (which would let a stolen token overwrite a working TOTP
        secret), and an "enroll" token cannot stand in for a verified second
        factor.
        """
        token = generate_mfa_session_token()
        await cache_service.set(
            f"mfa_session:{token}",
            # tenant_id travels with the session so _resolve_mfa_session can
            # bind RLS before its User lookup — it comes from the DB-loaded
            # `user` passed in here, never a client-supplied claim, the same
            # trust boundary set_tenant_context's other callers rely on.
            json.dumps(
                {"user_id": str(user.id), "tenant_id": str(user.tenant_id), "purpose": purpose}
            ),
            ttl=MFA_SESSION_TTL_SECONDS,
        )
        return token

    async def _resolve_mfa_session(self, token: str, expected_purpose: str) -> User:
        raw = await cache_service.get(f"mfa_session:{token}")
        if raw is None:
            raise self._mfa_session_expired_error()

        try:
            payload = json.loads(raw)
            user_id = uuid.UUID(payload["user_id"])
            tenant_id = uuid.UUID(payload["tenant_id"])
            purpose = payload["purpose"]
        except (ValueError, TypeError, KeyError) as exc:
            raise self._mfa_session_expired_error() from exc

        # Same error as an expired session on purpose — a distinct code would
        # tell an attacker holding a token exactly which flow it unlocks.
        if purpose != expected_purpose:
            raise self._mfa_session_expired_error()

        # Without this, the User lookup below runs with no tenant context
        # bound and FORCE ROW LEVEL SECURITY silently returns zero rows —
        # every MFA enroll/verify call would read as "session expired"
        # regardless of the token being perfectly valid. Reproduced live:
        # every Owner/Manager/Super Admin login hung at mandatory first-time
        # enrollment with a 401, since those roles never reach here any other
        # way (no access token exists yet to bind tenant context from).
        await rls.set_tenant_context(self.session, tenant_id)

        user = await self._get_user_by_id(user_id)
        if user is None or not user.is_active:
            raise self._mfa_session_expired_error()
        return user

    async def issue_tokens(
        self, user: User, ip_address_hash: str, user_agent: str = "", role: Role | None = None
    ) -> TokenResponse:
        # Public because the OTP and social sign-in services mint sessions
        # through the same path — one place that writes a `sessions` row,
        # one place that decides an access token's claims.
        #
        # complete_authentication() has already loaded the role to read
        # mfa_required — passing it through saves a redundant round trip.
        role = role or await self._get_role_by_id(user.role_id)
        access_token = create_access_token(user.id, user.tenant_id, role.name)
        refresh_token = create_refresh_token()
        self.session.add(
            Session(
                user_id=user.id,
                tenant_id=user.tenant_id,
                refresh_token_hash=sha256_hex(refresh_token),
                ip_address_hash=ip_address_hash,
                user_agent=user_agent,
                expires_at=_utcnow() + timedelta(days=settings.JWT_REFRESH_TTL_DAYS),
                revoked=False,
            )
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.JWT_ACCESS_TTL_MINUTES * 60,
            role=role.name,
            tenant_id=str(user.tenant_id) if user.tenant_id else None,
        )

    def _log_login_success(self, user: User, ip_address_hash: str) -> None:
        self.session.add(
            AuditLog(
                tenant_id=user.tenant_id,
                user_id=user.id,
                action="login_success",
                resource_type="user",
                resource_id=user.id,
                ip_address_hash=ip_address_hash,
            )
        )

    async def _register_failed_login(self, user: User, ip_address_hash: str) -> None:
        user.failed_login_count += 1
        self.session.add(
            AuditLog(
                tenant_id=user.tenant_id,
                user_id=user.id,
                action="login_failed",
                resource_type="user",
                resource_id=user.id,
                ip_address_hash=ip_address_hash,
            )
        )
        if user.failed_login_count >= FAILED_LOGIN_LOCKOUT_THRESHOLD:
            user.locked_until = _utcnow() + timedelta(minutes=ACCOUNT_LOCKOUT_MINUTES)
            self.session.add(
                AuditLog(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    action="account_locked",
                    resource_type="user",
                    resource_id=user.id,
                    ip_address_hash=ip_address_hash,
                )
            )
            await messaging_service.send_account_locked_email(
                decrypt_pii(user.encrypted_email), user.locked_until
            )
        await self.session.commit()

    async def _revoke_all_sessions(self, user_id: uuid.UUID) -> None:
        result = await self.session.execute(
            select(Session).where(Session.user_id == user_id, Session.revoked.is_(False))
        )
        for existing_session in result.scalars().all():
            existing_session.revoked = True

    async def _bump_tokens_valid_from(self, user_id: uuid.UUID) -> None:
        """Revoke every externally-issued (Supabase/Firebase) token for this
        user by moving the watermark their `iat` is compared against.

        A plain UPDATE, not a SELECT-then-set: the User row is not otherwise
        loaded on this path, and there is nothing else to do with it here.
        """
        await self.session.execute(
            update(User).where(User.id == user_id).values(tokens_valid_from=_utcnow())
        )

    @staticmethod
    def _invalid_credentials_error() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_CREDENTIALS",
                    "message": "Invalid email or password.",
                }
            },
        )

    @staticmethod
    def _account_locked_error() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "error": {
                    "code": "ACCOUNT_LOCKED",
                    "message": "Account temporarily locked. Try again in 60 minutes.",
                }
            },
        )

    @staticmethod
    def _account_deactivated_error() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "ACCOUNT_DEACTIVATED",
                    "message": "This account has been deactivated. Contact your restaurant owner.",
                }
            },
        )

    @staticmethod
    def _mfa_session_expired_error() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "MFA_SESSION_EXPIRED",
                    "message": "Your login session expired. Please log in again.",
                }
            },
        )

    @staticmethod
    def _refresh_invalid_error() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "REFRESH_TOKEN_INVALID",
                    "message": "Your session has expired. Please log in again.",
                }
            },
        )

    @staticmethod
    def _reset_token_invalid_error() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "RESET_TOKEN_INVALID",
                    "message": "This reset link is invalid or has expired.",
                }
            },
        )

    async def _get_user_by_email_hash(self, email_hash: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email_hash == email_hash))
        return result.scalar_one_or_none()

    async def _get_user_by_identifier_hash(
        self, identifier_type: str, identifier_hash: str
    ) -> User | None:
        """Resolve the credential to a tenant first, then read the row scoped.

        Login is the archetypal chicken-and-egg under RLS: the identifier is
        globally unique precisely so it can name an account before anyone knows
        which tenant that account belongs to. Querying `users` directly returns
        zero rows once the app stops connecting as a superuser, which surfaces
        as "invalid email or password" for a perfectly valid login.

        The bootstrap resolver returns NULL both for "no such identifier" and
        for "identifier belongs to a standard user" (tenant_id genuinely NULL)
        — those two cases are indistinguishable from its single uuid return.
        When it comes back NULL, always fall through to a direct null-tenant
        lookup rather than treating that as "not found": migration 0012 widens
        restaurant.users' RLS policy to admit `tenant_id IS NULL` rows
        unconditionally, so this needs no bypass and no context bound. This
        fallback always runs on a resolver miss (never skipped) so a
        genuinely-unknown identifier and a standard user's identifier cost the
        same number of round trips before login()'s constant-time password
        check — no new timing oracle distinguishing the two.
        """
        if identifier_type == "email":
            tenant_id = await bootstrap.tenant_for_user_email_hash(self.session, identifier_hash)
            column = User.email_hash
        else:
            tenant_id = await bootstrap.tenant_for_user_username_hash(
                self.session, identifier_hash
            )
            column = User.username_hash

        if tenant_id is not None:
            await rls.set_tenant_context(self.session, tenant_id)
            result = await self.session.execute(select(User).where(column == identifier_hash))
            return result.scalar_one_or_none()

        result = await self.session.execute(
            select(User).where(column == identifier_hash, User.tenant_id.is_(None))
        )
        return result.scalar_one_or_none()

    async def _get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def _get_user_for_reset(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID | None
    ) -> User | None:
        """Same tenant-binding dance as _resolve_mfa_session: a bare
        select(User).where(User.id == user_id) returns zero rows under RLS
        unless tenant context is bound first. tenant_id travels in the
        pending_reset Redis blob (set from the DB-loaded user at
        forgot_password() time, never a client-supplied value) for exactly
        this reason — same null-tenant fallback shape as
        _get_user_by_identifier_hash for a standard user."""
        if tenant_id is not None:
            await rls.set_tenant_context(self.session, tenant_id)
            result = await self.session.execute(select(User).where(User.id == user_id))
            return result.scalar_one_or_none()

        result = await self.session.execute(
            select(User).where(User.id == user_id, User.tenant_id.is_(None))
        )
        return result.scalar_one_or_none()

    async def _get_role_by_id(self, role_id: uuid.UUID) -> Role:
        result = await self.session.execute(select(Role).where(Role.id == role_id))
        role = result.scalar_one_or_none()
        if role is None:
            msg = f"Role {role_id} referenced by a user but missing from static.roles"
            raise RuntimeError(msg)
        return role

    async def _get_session_by_token_hash(self, token_hash: str) -> Session | None:
        """Resolve the tenant from the opaque refresh token, then read scoped.

        Unlike an access token there are no claims to read a tenant from — a
        refresh token is 384 bits of nothing — so the session row itself is the
        only thing that knows which tenant it belongs to.

        Same NULL-ambiguity fallback as `_get_user_by_identifier_hash`: a
        standard user's session row has `tenant_id IS NULL`, indistinguishable
        from "no such session" in the bootstrap resolver's single-uuid return.
        Migration 0012 widens restaurant.sessions' RLS policy the same way it
        widens restaurant.users, so the fallback needs no bypass.
        """
        tenant_id = await bootstrap.tenant_for_session_token_hash(self.session, token_hash)
        if tenant_id is not None:
            await rls.set_tenant_context(self.session, tenant_id)
            result = await self.session.execute(
                select(Session).where(Session.refresh_token_hash == token_hash)
            )
            return result.scalar_one_or_none()

        result = await self.session.execute(
            select(Session).where(
                Session.refresh_token_hash == token_hash, Session.tenant_id.is_(None)
            )
        )
        return result.scalar_one_or_none()
