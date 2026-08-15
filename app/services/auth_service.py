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
    generate_totp_secret,
    generate_verification_token,
    hash_password,
    totp_provisioning_uri,
    verify_password_constant_time,
    verify_totp_code,
)
from app.db import bootstrap, rls
from app.db.models.audit import AuditLog
from app.db.models.tenant import Tenant
from app.db.models.user import Role, Session, User
from app.schemas.auth import (
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
    StatusResponse,
    TenantLookupResponse,
    TokenResponse,
    UserLogin,
    UserRegister,
    VerifyEmailResponse,
)
from app.services import messaging_service
from app.services.identity_service import classify_staff_identifier

logger = structlog.get_logger(__name__)

PENDING_REGISTRATION_TTL_SECONDS = 86400  # verification link valid 24h
MIN_ZXCVBN_SCORE = 3

MFA_SESSION_TTL_SECONDS = 300  # 5 minutes, per Doc 3
MFA_MAX_ATTEMPTS = 3
MFA_CODE_REPLAY_WINDOW_SECONDS = 90  # blocks reusing a code across two 30s TOTP steps
FAILED_LOGIN_LOCKOUT_THRESHOLD = 10
ACCOUNT_LOCKOUT_MINUTES = 60


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
                    "restaurant_name": request.restaurant_name,
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

        owner_role = await self._get_owner_role()
        subdomain = await self._unique_subdomain(pending["restaurant_name"])

        # Tenant + Owner user + audit trail commit as one transaction.
        tenant = Tenant(
            id=uuid.uuid4(),
            subdomain=subdomain,
            name=pending["restaurant_name"],
            onboarding_state="email_verified",
            is_active=True,
        )
        # restaurant.tenants itself carries no RLS policy — it's the tenant
        # registry, not a tenant-scoped table — but users and audit_logs both
        # are, and their INSERTs below carry tenant.id in that column. Without
        # this, the app's RLS-restricted DB role rejects both inserts (a
        # brand-new tenant's rows satisfy no session's app.tenant_id yet).
        await rls.set_tenant_context(self.session, tenant.id)
        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            role_id=owner_role.id,
            email_hash=email_hash,
            encrypted_email=encrypt_pii(pending["email"]),
            username_hash=username_hash,
            encrypted_username=encrypt_pii(username) if username else None,
            hashed_password=pending["password_hash"],
            email_verified=True,
        )
        self.session.add(tenant)
        # Forces the tenant INSERT to run now rather than trusting flush-order
        # inference across mapped classes — same pattern as
        # identity_link_service._provision_customer's customer/IdentityLink
        # pair. Without it, users.tenant_id / audit_logs.tenant_id can violate
        # their FK constraint before tenant's own row is actually visible.
        await self.session.flush()
        self.session.add(user)
        # Same reasoning again: audit_logs.user_id is a FK onto this row.
        await self.session.flush()
        self.session.add(
            AuditLog(
                tenant_id=tenant.id,
                user_id=user.id,
                action="email_verified",
                resource_type="tenant",
                resource_id=tenant.id,
            )
        )
        await self.session.commit()
        await cache_service.delete(f"pending_reg:{token}")

        logger.info(
            "auth.register.verified", tenant_id=str(tenant.id), user_id=str(user.id)
        )
        return VerifyEmailResponse(status="verified", subdomain=subdomain)

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
            await self._register_failed_login(user, ip_address_hash)
            if user.locked_until is not None and user.locked_until > _utcnow():
                raise self._account_locked_error()
            raise self._invalid_credentials_error()

        # Checked only after the password verifies, so it cannot be used to
        # enumerate which accounts an Owner has deactivated.
        if not user.is_active:
            raise self._account_deactivated_error()

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
            logger.info("auth.login.password_verified", user_id=str(user.id), role=role.name)
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
                    event_metadata={"role": role.name},
                )
            )
            await self.session.commit()
            logger.info("auth.login.mfa_enrollment_required", user_id=str(user.id), role=role.name)
            return MFAEnrollmentRequiredResponse(
                status="mfa_enrollment_required",
                mfa_session_token=token,
                expires_in=MFA_SESSION_TTL_SECONDS,
                role=role.name,
            )

        tokens = await self._issue_tokens(user, ip_address_hash, role=role)
        self._log_login_success(user, ip_address_hash)
        await self.session.commit()
        logger.info("auth.login.success", user_id=str(user.id), role=role.name)
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

        tokens = await self._issue_tokens(user, ip_address_hash)
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
        tokens = await self._issue_tokens(user, ip_address_hash)
        self._log_login_success(user, ip_address_hash)
        await self.session.commit()
        logger.info("auth.mfa.enrolled", user_id=str(user.id))
        return tokens

    # --- AUTH-04: current-user identity ---------------------------------

    async def me(self, user: User) -> MeResponse:
        role = await self._get_role_by_id(user.role_id)
        return MeResponse(
            user_id=str(user.id),
            tenant_id=str(user.tenant_id),
            email=decrypt_pii(user.encrypted_email),
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

        tokens = await self._issue_tokens(user, ip_address_hash, user_agent)
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

    async def _issue_tokens(
        self, user: User, ip_address_hash: str, user_agent: str = "", role: Role | None = None
    ) -> TokenResponse:
        # login() has already loaded the role to read mfa_required — passing it
        # through saves a redundant round trip on the hot path.
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
            tenant_id=str(user.tenant_id),
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
        """
        if identifier_type == "email":
            tenant_id = await bootstrap.tenant_for_user_email_hash(self.session, identifier_hash)
            column = User.email_hash
        else:
            tenant_id = await bootstrap.tenant_for_user_username_hash(
                self.session, identifier_hash
            )
            column = User.username_hash

        if tenant_id is None:
            return None

        await rls.set_tenant_context(self.session, tenant_id)
        result = await self.session.execute(select(User).where(column == identifier_hash))
        return result.scalar_one_or_none()

    async def _get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
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
        """
        tenant_id = await bootstrap.tenant_for_session_token_hash(self.session, token_hash)
        if tenant_id is None:
            return None

        await rls.set_tenant_context(self.session, tenant_id)
        result = await self.session.execute(
            select(Session).where(Session.refresh_token_hash == token_hash)
        )
        return result.scalar_one_or_none()
