"""Unit tests for app/core/broadcast.py (DASH-01).

`subscribe()` polls `get_message(timeout=...)` rather than the blocking
`pubsub.listen()` — a real hang was reproduced live: a reload/shutdown with a
dashboard WebSocket connected left the worker stuck at "Waiting for
background tasks to complete" indefinitely, because `listen()`'s indefinite
network read did not reliably observe task cancellation. These tests assert
on that specific shape: `get_message` is awaited with a bounded timeout, a
`None` poll does not yield anything, and cancelling the consumer task while
inside the loop actually unwinds instead of hanging.
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import broadcast

TENANT_ID = uuid.uuid4()


def make_pubsub(get_message_results: list) -> MagicMock:
    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    pubsub.get_message = AsyncMock(side_effect=get_message_results)
    return pubsub


@pytest.mark.asyncio
async def test_subscribe_polls_with_bounded_timeout_not_blocking_listen(mocker):
    """The bug this guards against: a blocking `listen()` call has no
    cancellation checkpoint. Every get_message() call must pass a timeout."""
    pubsub = make_pubsub(
        [
            {"type": "message", "data": json.dumps({"type": "new_scan", "payload": {}})},
            asyncio.CancelledError(),
        ]
    )
    mocker.patch.object(broadcast._client, "pubsub", MagicMock(return_value=pubsub))

    received = []
    with pytest.raises(asyncio.CancelledError):
        async with broadcast.subscribe(TENANT_ID) as events:
            async for event in events:
                received.append(event)

    assert received == [{"type": "new_scan", "payload": {}}]
    for call in pubsub.get_message.call_args_list:
        assert call.kwargs["timeout"] == broadcast._POLL_INTERVAL_SECONDS
    pubsub.unsubscribe.assert_awaited_once()
    pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_subscribe_skips_none_polls_without_yielding(mocker):
    """A timed-out poll (no message) must not surface as an empty/garbage
    event — it is exactly the cancellation checkpoint the fix exists for."""
    pubsub = make_pubsub(
        [
            None,
            None,
            {"type": "message", "data": json.dumps({"type": "review_approved", "payload": {}})},
            asyncio.CancelledError(),
        ]
    )
    mocker.patch.object(broadcast._client, "pubsub", MagicMock(return_value=pubsub))

    received = []
    with pytest.raises(asyncio.CancelledError):
        async with broadcast.subscribe(TENANT_ID) as events:
            async for event in events:
                received.append(event)

    assert received == [{"type": "review_approved", "payload": {}}]


@pytest.mark.asyncio
async def test_subscribe_cancellation_still_unsubscribes_and_closes(mocker):
    """A cancelled consumer (uvicorn shutdown, client disconnect) must still
    run cleanup — the exact path that hung before this fix."""
    pubsub = make_pubsub([asyncio.CancelledError()])
    mocker.patch.object(broadcast._client, "pubsub", MagicMock(return_value=pubsub))

    with pytest.raises(asyncio.CancelledError):
        async with broadcast.subscribe(TENANT_ID) as events:
            async for _event in events:
                pass

    pubsub.unsubscribe.assert_awaited_once()
    pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_subscribe_malformed_message_logged_and_skipped(mocker):
    pubsub = make_pubsub(
        [
            {"type": "message", "data": "not-json"},
            {"type": "message", "data": json.dumps({"type": "new_scan", "payload": {}})},
            asyncio.CancelledError(),
        ]
    )
    mocker.patch.object(broadcast._client, "pubsub", MagicMock(return_value=pubsub))

    received = []
    with pytest.raises(asyncio.CancelledError):
        async with broadcast.subscribe(TENANT_ID) as events:
            async for event in events:
                received.append(event)

    assert received == [{"type": "new_scan", "payload": {}}]


@pytest.mark.asyncio
async def test_publish_event_is_best_effort_on_redis_failure(mocker):
    mocker.patch.object(broadcast._client, "publish", AsyncMock(side_effect=ConnectionError("down")))

    # Must not raise — the caller (a stamp scan, a review approval) must
    # succeed regardless of whether the dashboard-refresh nudge goes out.
    await broadcast.publish_event(TENANT_ID, "new_scan", {"branch_id": "x"})
