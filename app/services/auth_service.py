"""QuickBite — Owner/Staff: register, login, mfa, refresh, logout.

AUTH-01 implements registration + email verification. No User or Tenant row
exists until the email is verified: the pending registration lives in Redis
(24h TTL) keyed by the verification token, so unverified accounts can never
log in and abandoned signups expire on their own. Tenant + Owner user are
created in one transaction on verification.

Login/MFA (AUTH-02) and refresh rotation (AUTH-03) are not implemented yet.
"""

import json
import re
import uuid

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from zxcvbn import zxcvbn

from app.core import cache_service
from app.core.encryption import encrypt_pii, sha256_hex
from app.core.security import generate_verification_token, hash_password
from app.db.models.audit import AuditLog
from app.db.models.tenant import Tenant
from app.db.models.user import Role, User
from app.schemas.auth import RegisterResponse, UserRegister, VerifyEmailResponse
from app.services import messaging_service

logger = structlog.get_logger(__name__)

PENDING_REGISTRATION_TTL_SECONDS = 86400  # verification link valid 24h
MIN_ZXCVBN_SCORE = 3


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

        token = generate_verification_token()
        await cache_service.set(
            f"pending_reg:{token}",
            json.dumps(
                {
                    "email": email,
                    "password_hash": password_hash,
                    "name": request.name,
                    "restaurant_name": request.restaurant_name,
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
        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            role_id=owner_role.id,
            email_hash=email_hash,
            encrypted_email=encrypt_pii(pending["email"]),
            hashed_password=pending["password_hash"],
            email_verified=True,
        )
        self.session.add(tenant)
        self.session.add(user)
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

    async def _email_exists(self, email_hash: str) -> bool:
        result = await self.session.execute(select(User.id).where(User.email_hash == email_hash))
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
