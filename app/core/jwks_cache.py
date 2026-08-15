"""Async JWKS fetch + cache for Supabase's asymmetric signing keys.

Supabase signs access tokens with ES256/RS256 and publishes the public keys at
`<issuer>/.well-known/jwks.json`. Verification therefore needs a public key on
every request — and fetching one per request would put a third-party HTTP call
on the hot path of every authenticated endpoint.

Three cache layers, in lookup order:

1. **Process-local dict** (TTL = SUPABASE_JWKS_TTL_SECONDS, default 600s).
   The hot path. A healthy process makes one origin call per 10 minutes no
   matter the request rate. 600s is not arbitrary: Supabase caches the JWKS
   for 10 minutes at its own edge, so caching longer would push key revocation
   past the window they guarantee.
2. **Redis**, shared across workers, so a deploy or scale-up does not produce
   one origin fetch per process.
3. **Origin**, guarded by a Redis `SET NX` lock so concurrent misses across
   processes collapse into a single fetch.

Failure policy is deliberately asymmetric to the revocation check in
`get_current_user`: JWKS **fails open** onto the last-known-good copy, because
a Supabase outage must not log out every user holding a still-valid token. A
revoked-token check, by contrast, fails closed. Two caches, two directions,
for two different risks.
"""

import asyncio
import json
import time
from typing import Any

import httpx
import jwt
import structlog
from fastapi import HTTPException, status

from app.core import cache_service
from app.core.config import settings

logger = structlog.get_logger(__name__)

# Serve a stale copy for a day if the origin is unreachable. Far longer than
# the fresh TTL on purpose — this is the outage cushion, not a cache.
_STALE_TTL_SECONDS = 86_400
# After a failed fetch, skip the network entirely for this long so an outage
# does not add a 5s timeout to every single request.
_NEGATIVE_TTL_SECONDS = 30
# Floor between forced refreshes triggered by an unknown `kid`.
_MIN_REFETCH_GAP_SECONDS = 60
# A JWKS with thousands of keys is either broken or hostile.
_MAX_KEYS = 16
_FETCH_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
_LOCK_TTL_SECONDS = 10
_LOCK_POLL_ATTEMPTS = 3
_LOCK_POLL_INTERVAL = 0.1

_KEY_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail={
        "error": {
            "code": "AUTH_PROVIDER_UNAVAILABLE",
            "message": "Sign-in is temporarily unavailable. Please try again shortly.",
        }
    },
)

_UNKNOWN_KEY = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "error": {
            "code": "INVALID_TOKEN",
            "message": "Your session has expired. Please log in again.",
        }
    },
)


class _Entry:
    """Parsed keys plus the wall-clock time they were fetched."""

    __slots__ = ("fetched_at", "keys")

    def __init__(self, keys: dict[str, Any], fetched_at: float) -> None:
        self.keys = keys
        self.fetched_at = fetched_at


_local: _Entry | None = None
_lock = asyncio.Lock()


def _redis_key() -> str:
    return f"jwks:supabase:{settings.SUPABASE_PROJECT_REF}"


def _parse(raw: str) -> dict[str, Any]:
    """Turn a JWKS document into {kid: PyJWK}, parsing each key exactly once.

    Parsing per request would re-do the EC/RSA key construction on every call;
    doing it here means the hot path is a dict lookup.
    """
    document = json.loads(raw)
    keys: dict[str, Any] = {}
    for entry in document.get("keys", [])[:_MAX_KEYS]:
        kid = entry.get("kid")
        if not kid:
            continue
        try:
            keys[kid] = jwt.PyJWK(entry)
        except Exception as exc:  # noqa: BLE001 - one bad key must not void the set
            logger.warning("auth.jwks.key_parse_failed", kid=kid, error=str(exc))
    return keys


async def _fetch_from_origin() -> str:
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        response = await client.get(settings.supabase_jwks_url)
        response.raise_for_status()
        return response.text


async def _store(raw: str) -> _Entry:
    """Populate every layer from a freshly fetched document."""
    entry = _Entry(_parse(raw), time.monotonic())
    global _local  # noqa: PLW0603 - module-level cache is the point
    _local = entry
    key = _redis_key()
    await cache_service.set(key, raw, ttl=settings.SUPABASE_JWKS_TTL_SECONDS)
    await cache_service.set(f"{key}:stale", raw, ttl=_STALE_TTL_SECONDS)
    return entry


async def _refresh() -> _Entry:
    """Fetch under a cross-process lock, falling back to the stale copy.

    Callers must already hold `_lock`, which collapses concurrent misses within
    this process; the Redis lock collapses them across processes.
    """
    key = _redis_key()
    if await cache_service.exists(f"{key}:negative"):
        # A recent fetch failed. Skip the network so an outage costs a Redis
        # round trip rather than a 5s timeout on every request.
        return await _from_stale_or_fail()

    won_lock = await cache_service.set_if_absent(f"{key}:lock", "1", ttl=_LOCK_TTL_SECONDS)
    if not won_lock:
        # Someone else is fetching. Poll briefly for their result, then fetch
        # anyway — bounded waiting, so a crashed lock holder delays us by
        # 300ms rather than until the lock's TTL expires.
        for _ in range(_LOCK_POLL_ATTEMPTS):
            await asyncio.sleep(_LOCK_POLL_INTERVAL)
            cached = await cache_service.get(key)
            if cached:
                return await _store(cached)

    try:
        raw = await _fetch_from_origin()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        logger.error("auth.jwks.fetch_failed", url=settings.supabase_jwks_url, error=str(exc))
        await cache_service.set(f"{key}:negative", "1", ttl=_NEGATIVE_TTL_SECONDS)
        return await _from_stale_or_fail()
    return await _store(raw)


async def _from_stale_or_fail() -> _Entry:
    """Last-known-good keys, or 503.

    Serving stale keys during an outage is safe: these are public keys whose
    only job is to verify a signature. The real risk they carry is delayed
    revocation of a rotated key, which is a far smaller harm than logging out
    every user because Supabase had a bad minute.

    503 rather than 401 when there is nothing cached at all: a 401 would tell
    every client to discard a perfectly valid session, turning a provider
    outage into a mass logout that looks like a breach.
    """
    stale = await cache_service.get(f"{_redis_key()}:stale")
    if not stale:
        raise _KEY_UNAVAILABLE
    logger.warning("auth.jwks.serving_stale", project_ref=settings.SUPABASE_PROJECT_REF)
    return _Entry(_parse(stale), time.monotonic())


async def _current() -> _Entry:
    """Fresh-enough keys from whichever layer has them."""
    entry = _local
    if entry is not None and time.monotonic() - entry.fetched_at < settings.SUPABASE_JWKS_TTL_SECONDS:
        return entry

    async with _lock:
        # Re-check: another coroutine may have refreshed while we waited.
        entry = _local
        if (
            entry is not None
            and time.monotonic() - entry.fetched_at < settings.SUPABASE_JWKS_TTL_SECONDS
        ):
            return entry

        cached = await cache_service.get(_redis_key())
        if cached:
            return await _store(cached)
        return await _refresh()


async def get_signing_key(kid: str) -> Any:
    """Public key for `kid`, refreshing once if it looks like a rotation.

    Raises 401 when the key genuinely does not exist, 503 when the provider is
    unreachable and nothing is cached.
    """
    entry = await _current()
    key = entry.keys.get(kid)
    if key is not None:
        return key

    # Unknown kid with a fresh cache means rotation just happened. Force one
    # refresh — but throttled, and this throttle is load-bearing: without it an
    # attacker mints tokens carrying random `kid`s and turns every request into
    # an origin fetch, a self-inflicted denial of service on our own auth path.
    if time.monotonic() - entry.fetched_at < _MIN_REFETCH_GAP_SECONDS:
        logger.info("auth.jwks.unknown_kid_throttled", kid=kid)
        raise _UNKNOWN_KEY

    async with _lock:
        await cache_service.delete(_redis_key())  # make peers refresh too
        refreshed = await _refresh()
    key = refreshed.keys.get(kid)
    if key is None:
        logger.info("auth.jwks.unknown_kid", kid=kid)
        raise _UNKNOWN_KEY
    return key


def reset_cache() -> None:
    """Drop the process-local cache. For tests and for a manual rotation kick."""
    global _local  # noqa: PLW0603
    _local = None
