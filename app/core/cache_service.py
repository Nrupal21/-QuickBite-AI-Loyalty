"""QuickBite — Redis cache service: get, set with TTL, delete, incr with TTL, exists.

Connection pool capped at 50 (Doc 2 INFRA-03). Same Redis instance backs the
Celery broker, configured separately in tasks/.
"""

import redis.asyncio as redis

from app.core.config import settings

_pool = redis.ConnectionPool.from_url(settings.REDIS_URL, max_connections=50)
_client = redis.Redis(connection_pool=_pool)


async def get(key: str) -> str | None:
    value = await _client.get(key)
    return value.decode() if value is not None else None


async def set(key: str, value: str, ttl: int | None = None) -> None:
    await _client.set(key, value, ex=ttl)


async def set_if_absent(key: str, value: str, ttl: int) -> bool:
    """SET NX EX — returns True only for the caller that created the key.

    The cross-process single-flight primitive: when N workers all miss the
    JWKS cache at once, exactly one wins this and fetches from the origin
    while the rest wait on the result. `ttl` is mandatory because a lock
    without expiry becomes permanent the moment its holder crashes.
    """
    return bool(await _client.set(key, value, ex=ttl, nx=True))


async def delete(key: str) -> None:
    await _client.delete(key)


async def incr(key: str, ttl: int | None = None) -> int:
    count = await _client.incr(key)
    if count == 1 and ttl is not None:
        await _client.expire(key, ttl)
    return count


async def exists(key: str) -> bool:
    return bool(await _client.exists(key))


async def queue_depth(queue_name: str = "celery") -> int:
    """Pending-task count for a Celery Redis-transport queue — LLEN on the
    queue's own list key, which is how Celery's default Redis transport
    stores an undelivered task. `celery_app.py` sets no custom queue name,
    so "celery" is the one queue this app ever uses."""
    return await _client.llen(queue_name)
