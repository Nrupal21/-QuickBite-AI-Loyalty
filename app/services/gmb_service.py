"""QuickBite — Google My Business reply posting (REVIEW-02).

Posting only. OAuth connect/refresh and inbound review sync belong to
REVIEW-03 — this module assumes a `GMBProfile` row already carries a usable
access token and does not attempt to obtain or refresh one. When no profile
is connected, or the stored token has expired, `post_review_reply` raises
`GMBNotConnected` / `GMBTokenExpired` so the caller (the Celery task) can log
and back off instead of crashing.
"""

from datetime import datetime, timezone

import httpx
import structlog

from app.core.encryption import decrypt_pii
from app.db.models.reputation import GMBProfile

logger = structlog.get_logger(__name__)

_GMB_REPLY_URL = (
    "https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews/{review_id}/reply"
)
_REQUEST_TIMEOUT_SECONDS = 10.0


class GMBNotConnected(RuntimeError):
    """No GMBProfile exists for this branch — nothing to post to."""


class GMBTokenExpired(RuntimeError):
    """The stored access token has expired. Refresh is REVIEW-03's scope."""


async def post_review_reply(profile: GMBProfile, external_review_id: str, reply_text: str) -> None:
    """POST an owner reply to a Google review via the GMB API.

    Raises on any failure — the caller decides whether that means "retry the
    Celery task" or "log and give up", not this function.
    """
    if not profile.is_connected:
        raise GMBNotConnected

    if profile.token_expires_at <= datetime.now(timezone.utc):
        raise GMBTokenExpired

    access_token = decrypt_pii(profile.encrypted_access_token)
    url = _GMB_REPLY_URL.format(
        account_id=profile.gmb_account_id,
        location_id=profile.gmb_location_id,
        review_id=external_review_id,
    )

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.put(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"comment": reply_text},
        )
    response.raise_for_status()

    logger.info(
        "gmb.reply.posted",
        branch_id=str(profile.branch_id),
        tenant_id=str(profile.tenant_id),
    )
