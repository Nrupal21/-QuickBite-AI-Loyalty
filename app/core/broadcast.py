"""QuickBite — Redis pub/sub for the dashboard WebSocket stream (DASH-01).

`GET /dashboard/stats` is a poll; `WebSocket /dashboard/stream` is the push
side that tells an open dashboard tab to re-poll (or update a badge) the
moment something happens, instead of the browser guessing an interval. Redis
pub/sub — not an in-process broadcast list — because the app runs as more
than one worker process/container: a scan landing on worker A has to reach a
dashboard's WebSocket connection that happens to be held open on worker B.

One channel per tenant (`dashboard:events:{tenant_id}`), never a global
channel fanned out and filtered client-side — a filter bug there would leak
one tenant's event stream to another tenant's open dashboard tab, the same
class of mistake row-level security exists to rule out for queries.

Redis pub/sub is fire-and-forget (no replay, no delivery guarantee) — the
right trade for a "something changed, maybe re-poll" signal where `GET
/dashboard/stats` is always the source of truth. A queue with persistence
would be the wrong tool: a dashboard tab that was closed for an hour does not
need 40 queued `new_scan` events replayed at it.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_pool = redis.ConnectionPool.from_url(settings.REDIS_URL, max_connections=50)
_client = redis.Redis(connection_pool=_pool)

# How long each get_message() poll blocks before returning None. Bounds how
# quickly a cancelled task (uvicorn shutdown, client disconnect) actually
# unwinds — pubsub.listen()'s indefinite blocking read does not reliably
# observe asyncio.CancelledError on every redis-py version, which surfaced as
# a real hang: WatchFiles reload with a dashboard tab connected left the
# worker stuck at "Waiting for background tasks to complete" indefinitely,
# needing a manual restart. Polling gives cancellation a checkpoint at least
# this often instead of blocking on one indefinite network read.
_POLL_INTERVAL_SECONDS = 1.0


def _channel(tenant_id: uuid.UUID) -> str:
    return f"dashboard:events:{tenant_id}"


async def publish_event(tenant_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> None:
    """Best-effort — a dropped dashboard notification must never fail the
    request that triggered it (a stamp scan, a review approval). The
    WebSocket side has nothing to retry against here; the next `GET
    /dashboard/stats` poll is always correct regardless."""
    try:
        await _client.publish(
            _channel(tenant_id), json.dumps({"type": event_type, "payload": payload})
        )
    except Exception as exc:  # noqa: BLE001 — deliberately broad, see docstring
        logger.warning("dashboard.broadcast.publish_failed", event_type=event_type, error=str(exc))


@asynccontextmanager
async def subscribe(tenant_id: uuid.UUID) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
    """Scoped subscription for one WebSocket connection's lifetime.

    Yields an async iterator of decoded `{"type": ..., "payload": ...}`
    messages; malformed messages (should not happen — this process is the
    only publisher) are logged and skipped rather than killing the socket.
    """
    pubsub = _client.pubsub()
    await pubsub.subscribe(_channel(tenant_id))

    async def _messages() -> AsyncIterator[dict[str, Any]]:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_POLL_INTERVAL_SECONDS
            )
            if message is None:
                continue  # poll timed out, no event — loop back for a cancellation check
            try:
                yield json.loads(message["data"])
            except (TypeError, ValueError) as exc:
                logger.warning("dashboard.broadcast.decode_failed", error=str(exc))

    try:
        yield _messages()
    finally:
        await pubsub.unsubscribe(_channel(tenant_id))
        await pubsub.aclose()
