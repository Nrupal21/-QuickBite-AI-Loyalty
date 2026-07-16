"""Unit tests for AES-256-GCM PII encryption (AUTH-01 / SEC-09 groundwork)."""

import pytest

from app.core.encryption import decrypt_pii, encrypt_pii, sha256_hex


def test_encrypt_decrypt_round_trip():
    plaintext = "+919876543210"
    token = encrypt_pii(plaintext)
    assert token != plaintext
    assert token.startswith("v1:")
    assert decrypt_pii(token) == plaintext


def test_unique_nonce_per_call():
    a = encrypt_pii("same value")
    b = encrypt_pii("same value")
    assert a != b  # random IV per call — identical plaintexts must differ
    assert decrypt_pii(a) == decrypt_pii(b) == "same value"


def test_ciphertext_is_not_readable():
    token = encrypt_pii("owner@restaurant.in")
    assert "owner" not in token
    assert "restaurant" not in token


def test_unknown_key_version_rejected():
    token = encrypt_pii("secret")
    tampered = "v9" + token[2:]
    with pytest.raises(ValueError, match="version"):
        decrypt_pii(tampered)


def test_sha256_hex_is_deterministic():
    assert sha256_hex("a@b.com") == sha256_hex("a@b.com")
    assert len(sha256_hex("a@b.com")) == 64
