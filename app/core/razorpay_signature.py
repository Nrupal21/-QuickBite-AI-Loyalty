"""Razorpay webhook + payment signature verification (SEC-10 equivalent).

A webhook carries no bearer token, so this signature *is* the authentication
for `/webhooks/razorpay`. It must run before the payload is parsed, let alone
acted on — a forged body that reaches the handler can move a tenant onto the
enterprise plan for free.

Deliberately a hand-rolled HMAC rather than `razorpay.Utility`: the SDK helper
raises its own exception type and takes `str`, which invites decoding the body
before verifying it. Razorpay computes the HMAC over the **exact bytes** it
sent, so any decode/re-encode round trip (or FastAPI parsing the JSON and the
handler re-serialising it) changes the digest and every legitimate webhook
starts failing. Keeping this a pure `bytes -> bool` function makes that
mistake hard to make and the function trivial to test.
"""

import hashlib
import hmac

import structlog
from fastapi import HTTPException, status

logger = structlog.get_logger(__name__)

SIGNATURE_HEADER = "X-Razorpay-Signature"
EVENT_ID_HEADER = "X-Razorpay-Event-Id"

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
    """HMAC-SHA256 of the raw body, keyed by the webhook secret, as lowercase hex."""
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def is_valid_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    """Constant-time signature check. False for any missing input.

    An empty secret returns False rather than accepting everything: a
    misconfigured deployment must reject webhooks, not authenticate all of them.
    """
    if not signature or not secret:
        return False
    # compare_digest, never `==`: a byte-by-byte comparison leaks the position
    # of the first mismatch through timing, which is enough to forge a digest
    # one character at a time.
    return hmac.compare_digest(compute_signature(raw_body, secret), signature)


def verify_webhook_signature(raw_body: bytes, signature: str | None, secret: str) -> None:
    """Raise 401 unless `signature` authenticates `raw_body`.

    `raw_body` must be the bytes exactly as received — `await request.body()`,
    before any parsing.
    """
    if is_valid_signature(raw_body, signature, secret):
        return
    # No payload, no signature, no secret in the log line: this fires on
    # attacker-controlled input and the log is a lower-trust sink than the DB.
    logger.warning(
        "billing.webhook.signature_invalid",
        signature_present=bool(signature),
        secret_configured=bool(secret),
        body_bytes=len(raw_body),
    )
    raise _INVALID_SIGNATURE


def is_valid_payment_signature(
    order_id: str, payment_id: str, signature: str | None, key_secret: str
) -> bool:
    """Verify a Checkout callback signature: HMAC over "<order_id>|<payment_id>".

    Note this one is keyed by RAZORPAY_KEY_SECRET, while webhooks are keyed by
    RAZORPAY_WEBHOOK_SECRET. Swapping the two is the single most common cause
    of "valid signature rejected" and costs hours to spot, because both values
    look alike and neither is echoed anywhere.
    """
    if not signature or not key_secret:
        return False
    expected = hmac.new(
        key_secret.encode("utf-8"),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
