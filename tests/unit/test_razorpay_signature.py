"""Unit tests for Razorpay webhook/payment signature verification.

This signature IS the authentication for /webhooks/razorpay — there is no
bearer token, so every failure mode here is a direct security boundary.
"""

import hashlib
import hmac

import pytest
from fastapi import HTTPException

from app.core.razorpay_signature import (
    compute_signature,
    is_valid_payment_signature,
    is_valid_signature,
    verify_webhook_signature,
)

SECRET = "whsec_test_secret"
BODY = b'{"event":"subscription.activated","payload":{}}'


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_compute_signature_matches_manual_hmac():
    assert compute_signature(BODY, SECRET) == _sign(BODY)


def test_valid_signature_is_accepted():
    assert is_valid_signature(BODY, _sign(BODY), SECRET) is True


def test_tampered_body_is_rejected():
    """The signature was computed over the ORIGINAL body — any mutation, even
    appending whitespace, must invalidate it."""
    assert is_valid_signature(BODY + b" ", _sign(BODY), SECRET) is False


def test_wrong_secret_is_rejected():
    assert is_valid_signature(BODY, _sign(BODY, secret="wrong"), SECRET) is False


def test_missing_signature_is_rejected():
    assert is_valid_signature(BODY, None, SECRET) is False


def test_missing_secret_is_rejected():
    """A misconfigured deployment (empty secret) must reject every webhook,
    never authenticate all of them by accident."""
    assert is_valid_signature(BODY, _sign(BODY), "") is False


def test_verify_webhook_signature_raises_401_on_mismatch():
    with pytest.raises(HTTPException) as exc_info:
        verify_webhook_signature(BODY, "forged-signature", SECRET)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_verify_webhook_signature_passes_silently_when_valid():
    verify_webhook_signature(BODY, _sign(BODY), SECRET)  # must not raise


# --- Checkout callback (order_id|payment_id) signature ------------------


def test_payment_signature_valid():
    order_id, payment_id = "order_abc", "pay_xyz"
    key_secret = "key_secret_value"
    expected = hmac.new(
        key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()

    assert is_valid_payment_signature(order_id, payment_id, expected, key_secret) is True


def test_payment_signature_rejects_swapped_secret():
    """The single most common real-world bug: pasting RAZORPAY_KEY_SECRET
    where RAZORPAY_WEBHOOK_SECRET belongs, or vice versa."""
    order_id, payment_id = "order_abc", "pay_xyz"
    webhook_secret = "whsec_test_secret"
    signature_from_webhook_secret = hmac.new(
        webhook_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()

    assert (
        is_valid_payment_signature(
            order_id, payment_id, signature_from_webhook_secret, "key_secret_value"
        )
        is False
    )
