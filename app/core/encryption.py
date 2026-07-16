"""QuickBite — AES-256-GCM encryption + key versioning for all PII.

All PII (phone, email, name, address, GSTIN/PAN) must be encrypted before
storage — TIER 3 fields store both `encrypt_pii()` output and a
`sha256_hex()` lookup hash. Ciphertext format: `v1:<base64(nonce + ct)>`,
random 96-bit nonce per call, so values can be re-encrypted under a future
ENCRYPTION_KEY_V2 without a flag-day migration.
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_KEY_VERSION = "v1"
_NONCE_BYTES = 12  # 96-bit nonce per NIST SP 800-38D


def _key_v1() -> bytes:
    key = base64.b64decode(settings.ENCRYPTION_KEY_V1)
    if len(key) != 32:
        msg = "ENCRYPTION_KEY_V1 must decode to exactly 32 bytes (AES-256)"
        raise ValueError(msg)
    return key


def encrypt_pii(plaintext: str, key: bytes | None = None) -> str:
    """Encrypt a PII value. Returns `v1:<base64(nonce + ciphertext)>`."""
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key or _key_v1()).encrypt(nonce, plaintext.encode(), None)
    return f"{_KEY_VERSION}:{base64.b64encode(nonce + ciphertext).decode()}"


def decrypt_pii(token: str, key: bytes | None = None) -> str:
    """Decrypt an `encrypt_pii()` value. Raises ValueError on unknown version."""
    version, _, payload = token.partition(":")
    if version != _KEY_VERSION or not payload:
        msg = f"Unknown encryption key version: {version!r}"
        raise ValueError(msg)
    raw = base64.b64decode(payload)
    nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    return AESGCM(key or _key_v1()).decrypt(nonce, ciphertext, None).decode()


def sha256_hex(value: str) -> str:
    """TIER 2/3 lookup hash. Emails must be lowercased by the caller first."""
    return hashlib.sha256(value.encode()).hexdigest()
