"""QuickBite — Customer OTP: generate, Redis store, verify, rate-check.

NEW-OTP-01/02. `secrets.token_digits` does not exist in the Python stdlib
(despite being named that way in the docs) — `secrets.choice` over digits
is the cryptographically secure equivalent, never `random.randint`. Stores
SHA-256(code) in Redis with a 300s TTL. Never stores the plaintext code.
"""

import json
import secrets
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.core.config import settings
from app.core.customer_security import create_customer_token
from app.core.encryption import sha256_hex
from app.core.security import generate_otp_code
from app.db import rls
from app.db.models.customer import Customer
from app.schemas.customer_auth import (
    OTPNewUserResponse,
    OTPRequest,
    OTPSentResponse,
    OTPVerifiedResponse,
    OTPVerify,
)
from app.services import messaging_service
from app.services.identity_service import classify_identifier

logger = structlog.get_logger(__name__)

REGISTRATION_TOKEN_TTL_SECONDS = 900  # 15 minutes to complete the registration form


def _lookup_value(identifier: str, identifier_type: str) -> str:
    return identifier.lower() if identifier_type == "email" else identifier


async def _get_customer(
    session: AsyncSession, tenant_id: uuid.UUID, identifier_hash: str, identifier_type: str
) -> Customer | None:
    hash_column = Customer.email_hash if identifier_type == "email" else Customer.phone_hash
    result = await session.execute(
        select(Customer).where(hash_column == identifier_hash, Customer.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def request_otp(
    request: OTPRequest, session: AsyncSession
) -> OTPSentResponse | OTPNewUserResponse:
    identifier_type = classify_identifier(request.identifier)
    identifier_hash = sha256_hex(_lookup_value(request.identifier, identifier_type))

    cooldown_key = f"otp_cooldown:{request.tenant_id}:{identifier_hash}"
    if await cache_service.exists(cooldown_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code": "OTP_RATE_LIMIT",
                    "message": "Please wait before requesting another code.",
                    "retry_after_seconds": settings.CUSTOMER_OTP_RATE_LIMIT_SECONDS,
                }
            },
            headers={"Retry-After": str(settings.CUSTOMER_OTP_RATE_LIMIT_SECONDS)},
        )

    daily_key = f"otp_daily:{request.tenant_id}:{identifier_hash}"
    daily_count = await cache_service.incr(daily_key, ttl=86400)
    if daily_count > settings.CUSTOMER_OTP_DAILY_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code": "OTP_DAILY_LIMIT",
                    "message": "Daily limit reached. Please try again tomorrow.",
                }
            },
        )

    # customer.customers is RLS-protected and this request has no authenticated
    # principal yet — tenant_id came straight from the caller's own request
    # body, which is exactly what _get_customer() below needs to scope its
    # lookup to, so it's safe to bind right before that query runs.
    await rls.set_tenant_context(session, request.tenant_id)
    customer = await _get_customer(session, request.tenant_id, identifier_hash, identifier_type)
    await cache_service.set(cooldown_key, "1", ttl=settings.CUSTOMER_OTP_RATE_LIMIT_SECONDS)

    if customer is None:
        token = secrets.token_urlsafe(32)
        await cache_service.set(
            f"pending_customer_reg:{token}",
            json.dumps(
                {
                    "tenant_id": str(request.tenant_id),
                    "identifier": request.identifier,
                    "identifier_type": identifier_type,
                }
            ),
            ttl=REGISTRATION_TOKEN_TTL_SECONDS,
        )
        logger.info(
            "customer.otp.new_user", tenant_id=str(request.tenant_id), identifier_type=identifier_type
        )
        return OTPNewUserResponse(registration_token=token)

    otp = generate_otp_code()
    await cache_service.set(
        f"otp:{request.tenant_id}:{identifier_hash}",
        sha256_hex(otp),
        ttl=settings.CUSTOMER_OTP_TTL_SECONDS,
    )
    customer.otp_attempts = 0
    await session.commit()

    if identifier_type == "phone":
        await messaging_service.send_otp_sms(request.identifier, otp)
    else:
        await messaging_service.send_otp_email(request.identifier, otp)

    logger.info("customer.otp.sent", tenant_id=str(request.tenant_id), identifier_type=identifier_type)
    return OTPSentResponse()


async def verify_otp(request: OTPVerify, session: AsyncSession) -> tuple[OTPVerifiedResponse, str]:
    identifier_type = classify_identifier(request.identifier)
    identifier_hash = sha256_hex(_lookup_value(request.identifier, identifier_type))

    await rls.set_tenant_context(session, request.tenant_id)
    customer = await _get_customer(session, request.tenant_id, identifier_hash, identifier_type)
    if customer is None:
        raise _code_expired_error()

    otp_key = f"otp:{request.tenant_id}:{identifier_hash}"
    stored_hash = await cache_service.get(otp_key)
    if stored_hash is None:
        raise _code_expired_error()

    if sha256_hex(request.otp_code) != stored_hash:
        customer.otp_attempts += 1
        if customer.otp_attempts >= settings.CUSTOMER_OTP_MAX_ATTEMPTS:
            await cache_service.delete(otp_key)
            customer.otp_attempts = 0
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "OTP_TOO_MANY_ATTEMPTS",
                        "message": "Too many attempts. Please request a new code.",
                    }
                },
            )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "OTP_CODE_INVALID",
                    "message": "Invalid verification code.",
                    "attempts_remaining": settings.CUSTOMER_OTP_MAX_ATTEMPTS - customer.otp_attempts,
                }
            },
        )

    await cache_service.delete(otp_key)
    customer.otp_attempts = 0
    customer.last_seen_at = datetime.now(timezone.utc)
    await session.commit()

    token = create_customer_token(customer.id, customer.tenant_id, customer.phone_hash)
    response = OTPVerifiedResponse(
        customer_id=str(customer.id),
        total_stamps_alltime=customer.total_stamps_alltime,
        current_reward_count=customer.current_reward_count,
    )
    logger.info("customer.otp.verified", customer_id=str(customer.id))
    return response, token


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
