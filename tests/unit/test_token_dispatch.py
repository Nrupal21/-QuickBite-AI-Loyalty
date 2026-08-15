"""Unit tests for `_classify` — the token-dispatch security core in
app/api/v1/dependencies/auth.py.

Every branch here decides which of three independent verifiers gets to check
a signature. A mistake in this file is not "one test fails" — it is "a token
signed by the wrong party authenticates as someone else." Every failure case
must produce the exact same 401 body: a differentiated error message would
tell an attacker which providers are configured and how far their forged
token got.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import HTTPException

from app.api.v1.dependencies.auth import _classify
from app.core.config import settings
from app.core.principal import AuthProvider

_EC_KEY = ec.generate_private_key(ec.SECP256R1())
_EC_PRIVATE_PEM = _EC_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_RSA_PRIVATE_PEM = _RSA_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


def _future_exp() -> int:
    return int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())


def _mint(
    alg: str,
    *,
    iss: str | None = None,
    headers: dict | None = None,
    key: str | bytes | None = None,
) -> str:
    claims = {"sub": str(uuid.uuid4()), "exp": _future_exp(), "iat": int(datetime.now(timezone.utc).timestamp())}
    if iss is not None:
        claims["iss"] = iss
    signing_key = key if key is not None else ("hs-secret" if alg == "HS256" else _EC_PRIVATE_PEM)
    return jwt.encode(claims, signing_key, algorithm=alg, headers=headers)


@pytest.fixture
def supabase_enabled(mocker):
    mocker.patch.object(settings, "SUPABASE_PROJECT_REF", "test-ref")
    return settings.supabase_issuer


@pytest.fixture
def firebase_enabled(mocker):
    mocker.patch.object(settings, "FIREBASE_PROJECT_ID", "test-project")
    mocker.patch.object(settings, "FIREBASE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    return settings.firebase_issuer


def test_local_token_with_no_issuer_classifies_as_local():
    token = _mint("HS256")
    assert _classify(token) is AuthProvider.LOCAL


def test_alg_none_is_rejected():
    forged = jwt.encode(
        {"sub": "x", "exp": _future_exp()}, key="", algorithm="none"
    )
    with pytest.raises(HTTPException) as exc_info:
        _classify(forged)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_TOKEN"


def test_jku_header_is_rejected():
    """A jku header points the verifier at an attacker-chosen key source."""
    token = _mint("HS256", headers={"jku": "https://evil.example/jwks.json"})
    with pytest.raises(HTTPException) as exc_info:
        _classify(token)
    assert exc_info.value.status_code == 401


def test_x5u_header_is_rejected():
    token = _mint("HS256", headers={"x5u": "https://evil.example/cert.pem"})
    with pytest.raises(HTTPException):
        _classify(token)


def test_unknown_issuer_is_rejected():
    token = _mint("HS256", iss="https://not-a-configured-provider.example/auth")
    with pytest.raises(HTTPException) as exc_info:
        _classify(token)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_TOKEN"


def test_supabase_token_classifies_as_supabase(supabase_enabled):
    token = _mint("ES256", iss=supabase_enabled)
    assert _classify(token) is AuthProvider.SUPABASE


def test_hs256_token_claiming_supabase_issuer_is_rejected(supabase_enabled):
    """Algorithm-confusion guard: an HS256 token must never reach the
    Supabase verifier, where its JWKS public key could be replayed as an
    HMAC secret."""
    token = _mint("HS256", iss=supabase_enabled)
    with pytest.raises(HTTPException) as exc_info:
        _classify(token)
    assert exc_info.value.status_code == 401


def test_supabase_issuer_prefix_injection_is_rejected(supabase_enabled):
    """Exact-equality guard: a substring/startswith match would be defeated
    by an issuer like this."""
    hostile_issuer = f"https://evil.example/#{supabase_enabled}"
    token = _mint("ES256", iss=hostile_issuer)
    with pytest.raises(HTTPException) as exc_info:
        _classify(token)
    assert exc_info.value.status_code == 401


def test_supabase_token_rejected_when_supabase_not_configured():
    """No SUPABASE_PROJECT_REF set -> the branch can never be selected, even
    with a syntactically perfect Supabase-shaped token."""
    token = _mint("ES256", iss="https://some-ref.supabase.co/auth/v1")
    with pytest.raises(HTTPException):
        _classify(token)


def test_firebase_token_classifies_as_firebase(firebase_enabled):
    token = _mint("RS256", iss=firebase_enabled, key=_RSA_PRIVATE_PEM)
    assert _classify(token) is AuthProvider.FIREBASE


def test_firebase_es256_alg_is_rejected(firebase_enabled):
    """Firebase only ever signs RS256 — an ES256 token claiming its issuer
    must not be routed to the Firebase verifier."""
    token = _mint("ES256", iss=firebase_enabled)
    with pytest.raises(HTTPException) as exc_info:
        _classify(token)
    assert exc_info.value.status_code == 401


def test_every_rejection_has_identical_error_body(supabase_enabled):
    """A differentiated 401 body would be a probing oracle for which
    providers are enabled and why a given token failed."""
    bad_tokens = [
        jwt.encode({"sub": "x", "exp": _future_exp()}, key="", algorithm="none"),
        _mint("HS256", iss=supabase_enabled),
        _mint("HS256", headers={"jku": "https://evil.example/jwks.json"}),
        _mint("HS256", iss="https://unknown.example/auth"),
    ]
    bodies = set()
    for token in bad_tokens:
        with pytest.raises(HTTPException) as exc_info:
            _classify(token)
        assert exc_info.value.status_code == 401
        bodies.add(str(exc_info.value.detail))
    assert len(bodies) == 1
