"""Unit tests for Supabase token verification, minting real ES256 tokens
against a locally generated keypair and a mocked JWKS response.

The JWKS cache is exercised through its public surface (get_signing_key) with
cache_service mocked as a plain in-memory dict, rather than mocking jwt.decode
— that would test nothing about whether a forged claim is actually rejected.
"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from jwt.utils import to_base64url_uint

from app.core import jwks_cache
from app.core.config import settings
from app.core.supabase_auth import verify_supabase_token

_KID = "test-kid-1"
_EC_KEY = ec.generate_private_key(ec.SECP256R1())
_EC_PRIVATE_PEM = _EC_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


def _jwk_document() -> dict:
    public_numbers = _EC_KEY.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "kid": _KID,
                "x": to_base64url_uint(public_numbers.x).decode(),
                "y": to_base64url_uint(public_numbers.y).decode(),
                "use": "sig",
                "alg": "ES256",
            }
        ]
    }


def _mint(*, kid: str | None = _KID, role: str = "authenticated", **claim_overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "iss": settings.supabase_issuer,
        "role": role,
        "session_id": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    claims.update(claim_overrides)
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, _EC_PRIVATE_PEM, algorithm="ES256", headers=headers)


@pytest.fixture(autouse=True)
def _configure_and_reset(mocker):
    mocker.patch.object(settings, "SUPABASE_PROJECT_REF", "test-ref")
    jwks_cache.reset_cache()
    yield
    jwks_cache.reset_cache()


@pytest.fixture
def fake_redis(mocker):
    """cache_service backed by a plain dict — exercises the real cache-layer
    logic in jwks_cache without a real Redis."""
    store: dict[str, str] = {}

    async def _get(key: str) -> str | None:
        return store.get(key)

    async def _set(key: str, value: str, ttl: int | None = None) -> None:
        store[key] = value

    async def _set_if_absent(key: str, value: str, ttl: int) -> bool:
        if key in store:
            return False
        store[key] = value
        return True

    async def _delete(key: str) -> None:
        store.pop(key, None)

    async def _exists(key: str) -> bool:
        return key in store

    mocker.patch("app.core.jwks_cache.cache_service.get", side_effect=_get)
    mocker.patch("app.core.jwks_cache.cache_service.set", side_effect=_set)
    mocker.patch("app.core.jwks_cache.cache_service.set_if_absent", side_effect=_set_if_absent)
    mocker.patch("app.core.jwks_cache.cache_service.delete", side_effect=_delete)
    mocker.patch("app.core.jwks_cache.cache_service.exists", side_effect=_exists)
    return store


@pytest.fixture
def mock_jwks_endpoint(mocker, fake_redis):
    """Fetching from origin returns our locally generated JWKS document."""
    import json

    response = mocker.MagicMock()
    response.text = json.dumps(_jwk_document())
    response.raise_for_status = mocker.MagicMock()

    async def _fetch_from_origin():
        return response.text

    mocker.patch("app.core.jwks_cache._fetch_from_origin", side_effect=_fetch_from_origin)
    return response


@pytest.mark.asyncio
async def test_valid_supabase_token_is_accepted(mock_jwks_endpoint):
    token = _mint()
    claims = await verify_supabase_token(token)
    assert claims["role"] == "authenticated"


@pytest.mark.asyncio
async def test_service_role_token_is_rejected(mock_jwks_endpoint):
    """A service_role JWT is a full-privilege API key, signed by the same
    project key — it must never authenticate as an end user."""
    token = _mint(role="service_role")
    with pytest.raises(HTTPException) as exc_info:
        await verify_supabase_token(token)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_anon_role_token_is_rejected(mock_jwks_endpoint):
    token = _mint(role="anon")
    with pytest.raises(HTTPException):
        await verify_supabase_token(token)


@pytest.mark.asyncio
async def test_wrong_audience_is_rejected(mock_jwks_endpoint):
    token = _mint(aud="some-other-audience")
    with pytest.raises(HTTPException):
        await verify_supabase_token(token)


@pytest.mark.asyncio
async def test_expired_token_is_rejected(mock_jwks_endpoint):
    now = datetime.now(timezone.utc)
    token = _mint(exp=int((now - timedelta(minutes=5)).timestamp()))
    with pytest.raises(HTTPException):
        await verify_supabase_token(token)


@pytest.mark.asyncio
async def test_missing_kid_is_rejected(mock_jwks_endpoint):
    token = _mint(kid=None)
    with pytest.raises(HTTPException):
        await verify_supabase_token(token)


@pytest.mark.asyncio
async def test_unknown_kid_triggers_exactly_one_refetch(mock_jwks_endpoint, mocker):
    """Rotation just happened: the cache should refresh once, not repeatedly,
    for a single unknown kid within the throttle window."""
    fetch_spy = mocker.spy(jwks_cache, "_fetch_from_origin")
    token = _mint(kid="a-kid-not-in-the-document")

    with pytest.raises(HTTPException):
        await verify_supabase_token(token)

    assert fetch_spy.call_count == 1


@pytest.mark.asyncio
async def test_second_unknown_kid_within_throttle_does_not_refetch(mock_jwks_endpoint, mocker):
    mocker.patch.object(jwks_cache, "_MIN_REFETCH_GAP_SECONDS", 3600)
    fetch_spy = mocker.spy(jwks_cache, "_fetch_from_origin")

    with pytest.raises(HTTPException):
        await jwks_cache.get_signing_key("unknown-1")
    with pytest.raises(HTTPException):
        await jwks_cache.get_signing_key("unknown-2")

    assert fetch_spy.call_count == 1


@pytest.mark.asyncio
async def test_cache_hit_does_not_hit_the_network(mock_jwks_endpoint, mocker):
    await jwks_cache.get_signing_key(_KID)  # primes the cache
    fetch_spy = mocker.spy(jwks_cache, "_fetch_from_origin")

    await jwks_cache.get_signing_key(_KID)

    assert fetch_spy.call_count == 0


@pytest.mark.asyncio
async def test_origin_down_serves_stale_copy(fake_redis, mocker):
    """A prior successful fetch left a stale copy; a later outage must still
    serve it rather than logging every user out."""
    import json

    fake_redis[f"jwks:supabase:{settings.SUPABASE_PROJECT_REF}:stale"] = json.dumps(
        _jwk_document()
    )

    async def _boom():
        # httpx's real transport errors (ConnectError, TimeoutException, ...)
        # are all HTTPError subclasses — this is what jwks_cache actually
        # catches, unlike a bare builtin ConnectionError.
        raise httpx.ConnectError("origin unreachable")

    mocker.patch("app.core.jwks_cache._fetch_from_origin", side_effect=_boom)

    key = await jwks_cache.get_signing_key(_KID)
    assert key is not None


@pytest.mark.asyncio
async def test_cold_start_with_origin_down_returns_503(fake_redis, mocker):
    """No stale copy and the origin is unreachable: 503, never 401 — a 401
    would tell every client to discard a perfectly valid session."""

    async def _boom():
        # httpx's real transport errors (ConnectError, TimeoutException, ...)
        # are all HTTPError subclasses — this is what jwks_cache actually
        # catches, unlike a bare builtin ConnectionError.
        raise httpx.ConnectError("origin unreachable")

    mocker.patch("app.core.jwks_cache._fetch_from_origin", side_effect=_boom)

    with pytest.raises(HTTPException) as exc_info:
        await jwks_cache.get_signing_key(_KID)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == "AUTH_PROVIDER_UNAVAILABLE"
