"""Unit tests for Meta webhook signature verification.

This signature IS the authentication for /webhooks/whatsapp — there is no
bearer token, so every failure mode here is a direct security boundary.
Mirrors test_razorpay_signature.py's shape for the sibling webhook.
"""

import hashlib
import hmac

import pytest
from fastapi import HTTPException

from app.core.meta_signature import compute_signature, is_valid_signature, verify_webhook_signature

SECRET = "app_secret_test"
BODY = b'{"object":"whatsapp_business_account","entry":[]}'


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_compute_signature_matches_manual_hmac():
    assert compute_signature(BODY, SECRET) == _sign(BODY)


def test_valid_signature_is_accepted():
    assert is_valid_signature(BODY, _sign(BODY), SECRET) is True


def test_tampered_body_is_rejected():
    assert is_valid_signature(BODY + b" ", _sign(BODY), SECRET) is False


def test_wrong_secret_is_rejected():
    assert is_valid_signature(BODY, _sign(BODY, secret="wrong"), SECRET) is False


def test_missing_signature_is_rejected():
    assert is_valid_signature(BODY, None, SECRET) is False


def test_missing_secret_is_rejected():
    """A misconfigured deployment (empty META_APP_SECRET) must reject every
    webhook, never authenticate all of them by accident."""
    assert is_valid_signature(BODY, _sign(BODY), "") is False


def test_signature_without_sha256_prefix_is_rejected():
    """Meta always prefixes with `sha256=` — a bare hex digest (a plausible
    copy-paste mistake) must not accidentally compare equal."""
    bare_hex = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    assert is_valid_signature(BODY, bare_hex, SECRET) is False


def test_verify_webhook_signature_raises_401_on_mismatch():
    with pytest.raises(HTTPException) as exc_info:
        verify_webhook_signature(BODY, "sha256=forged", SECRET)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_verify_webhook_signature_passes_silently_when_valid():
    verify_webhook_signature(BODY, _sign(BODY), SECRET)  # must not raise
