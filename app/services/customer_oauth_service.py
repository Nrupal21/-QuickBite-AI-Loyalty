"""QuickBite — Customer sign-in via a verified Firebase ID token.

Powers the "Sign in with Google / Apple / Microsoft / GitHub / Twitter"
buttons on the customer login screen. Firebase's client SDK abstracts all
five federated providers into one ID token shape, so there is exactly one
verification path here regardless of which button the customer clicked —
no per-provider branching, and no `provider` field on the request.

Two branches:
- The Firebase subject is already linked (`restaurant.identity_links`) to a
  Customer -> sign them straight in, same response shape as an OTP verify.
- First time seeing this subject -> every Customer row requires a phone
  number (loyalty_service's geofence rate limiter and SMS/WhatsApp alerts
  both key off `phone_hash`), and Firebase never hands one back for an
  OAuth sign-in. This hands back a `registration_token` on the same shape
  customer_otp_service uses for a brand-new phone/email; `/customers/register`
  finishes the job and creates the identity_links row.
"""

import json
import secrets
from datetime import datetime, timezone

import structlog
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.core.customer_security import create_customer_token
from app.core.firebase_auth import verify_firebase_token
from app.core.principal import AuthProvider
from app.schemas.customer_auth import OAuthNewUserResponse, OAuthSignInRequest, OTPVerifiedResponse
from app.services import identity_link_service

logger = structlog.get_logger(__name__)

# Matches customer_otp_service.REGISTRATION_TOKEN_TTL_SECONDS — both are the
# same "finish registering" window, just reached from a different starting
# point (a verified Firebase subject instead of a phone/email OTP).
PENDING_REG_TTL_SECONDS = 900

_INVALID_TOKEN = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "error": {
            "code": "INVALID_TOKEN",
            "message": "Your session has expired. Please log in again.",
        }
    },
)


def _verified_email(claims: dict) -> str | None:
    """The email claim, but only when Firebase vouches for it.

    Mirrors identity_link_service._verified_email — duplicated rather than
    imported because that helper is module-private and this is the only
    other caller; promoting it to shared code isn't worth the indirection
    for one field.
    """
    email = claims.get("email")
    if email and claims.get("email_verified") is True:
        return email.strip().lower()
    return None


async def sign_in(
    payload: OAuthSignInRequest, session: AsyncSession
) -> tuple[OTPVerifiedResponse | OAuthNewUserResponse, str | None]:
    claims = await verify_firebase_token(payload.id_token)
    subject = claims.get("sub")
    if not subject:
        raise _INVALID_TOKEN
    subject = str(subject)

    customer = await identity_link_service.find_linked_customer(
        session, AuthProvider.FIREBASE, subject
    )
    if customer is not None:
        customer.last_seen_at = datetime.now(timezone.utc)
        await session.commit()
        token = create_customer_token(customer.id, customer.tenant_id, customer.phone_hash)
        response = OTPVerifiedResponse(
            customer_id=str(customer.id),
            total_stamps_alltime=customer.total_stamps_alltime,
            current_reward_count=customer.current_reward_count,
        )
        logger.info("customer.oauth.verified", customer_id=str(customer.id))
        return response, token

    email = _verified_email(claims)
    registration_token = secrets.token_urlsafe(32)
    await cache_service.set(
        f"pending_customer_reg:{registration_token}",
        json.dumps(
            {
                "tenant_id": str(payload.tenant_id),
                "identifier_type": "oauth",
                "oauth_provider": AuthProvider.FIREBASE.value,
                "oauth_subject": subject,
                "verified_email": email,
            }
        ),
        ttl=PENDING_REG_TTL_SECONDS,
    )
    logger.info("customer.oauth.new_user", tenant_id=str(payload.tenant_id))
    return OAuthNewUserResponse(registration_token=registration_token, email=email), None
