"""Meta webhook signature verification (X-Hub-Signature-256).

Meta signs the raw POST body with the app's client secret, HMAC-SHA256, sent
as `sha256=<hex>` in this header — the webhook's only authentication (no
bearer token reaches a server-to-server callback). Same bytes-before-parsing
discipline as razorpay_signature.py: verify the exact bytes Meta sent, before
anything decodes or re-serialises them.
"""

import hashlib
import hmac

import structlog
from fastapi import HTTPException, status

logger = structlog.get_logger(__name__)

SIGNATURE_HEADER = "X-Hub-Signature-256"

_INVALID_SIGNATURE = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "error": {
            "code": "INVALID_WEBHOOK_SIGNATURE",
            "message": "Webhook signature verification failed.",
        }
    },
)


def compute_signature(raw_body: bytes, secret: str) -> str:
    """HMAC-SHA256 of the raw body, keyed by the app secret, as `sha256=<hex>`."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def is_valid_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    """Constant-time signature check. False for any missing input — an
    unconfigured META_APP_SECRET must reject every webhook, not authenticate
    all of them."""
    if not signature or not secret:
        return False
    return hmac.compare_digest(compute_signature(raw_body, secret), signature)


def verify_webhook_signature(raw_body: bytes, signature: str | None, secret: str) -> None:
    """Raise 401 unless `signature` authenticates `raw_body`.

    `raw_body` must be the bytes exactly as received — `await request.body()`,
    before any parsing.
    """
    if is_valid_signature(raw_body, signature, secret):
        return
    logger.warning(
        "marketing.webhook.signature_invalid",
        signature_present=bool(signature),
        secret_configured=bool(secret),
        body_bytes=len(raw_body),
    )
    raise _INVALID_SIGNATURE
