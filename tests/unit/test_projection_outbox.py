"""Unit tests for projection_service.drain_pending — the outbox worker that
mirrors payment.projection_outbox into Firestore.

Firestore itself is mocked; these tests verify the drain loop's contract:
which rows get claimed, how failures back off, and that a Firestore write
failure never touches Postgres beyond the outbox row's own bookkeeping.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.db.models.outbox import FAILED, MAX_ATTEMPTS, PENDING, SENT, ProjectionOutbox
from app.services import projection_service

TENANT_ID = uuid.uuid4()


def make_row(**overrides) -> ProjectionOutbox:
    defaults = {
        "tenant_id": TENANT_ID,
        "aggregate_type": "subscription",
        "aggregate_id": uuid.uuid4(),
        "version": 100,
        "event_type": "subscription.activated",
        "payload": {"status": "active"},
        "status": PENDING,
        "attempts": 0,
        "next_attempt_at": datetime.now(UTC),
        "sent_at": None,
        "last_error": None,
    }
    defaults.update(overrides)
    row = ProjectionOutbox(**defaults)
    row.id = uuid.uuid4()
    return row


def make_session(rows: list[ProjectionOutbox]) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.fixture(autouse=True)
def _enabled(mocker):
    mocker.patch.object(settings, "FIRESTORE_PROJECTION_ENABLED", True)


@pytest.fixture
def fake_doc_ref():
    """A Firestore document reference whose transactional write always
    succeeds and reports no existing document (first write for this doc)."""
    doc_ref = MagicMock()
    snapshot = MagicMock()
    snapshot.exists = False
    doc_ref.get = AsyncMock(return_value=snapshot)
    return doc_ref


@pytest.mark.asyncio
async def test_disabled_projection_drains_nothing(mocker):
    mocker.patch.object(settings, "FIRESTORE_PROJECTION_ENABLED", False)
    session = make_session([make_row()])
    get_client = mocker.patch("app.services.projection_service.firestore_client.get_client")

    sent = await projection_service.drain_pending(session)

    assert sent == 0
    get_client.assert_not_called()


@pytest.mark.asyncio
async def test_no_pending_rows_drains_nothing(mocker):
    session = make_session([])
    mocker.patch("app.services.projection_service.firestore_client.get_client")

    assert await projection_service.drain_pending(session) == 0


@pytest.mark.asyncio
async def test_successful_write_marks_row_sent(mocker, fake_doc_ref):
    row = make_row()
    session = make_session([row])
    mocker.patch("app.services.projection_service.firestore_client.get_client")
    mocker.patch("app.services.projection_service._doc_ref", return_value=fake_doc_ref)
    mocker.patch(
        "app.services.projection_service._write_with_version_guard", AsyncMock(return_value=True)
    )

    sent = await projection_service.drain_pending(session)

    assert sent == 1
    assert row.status == SENT
    assert row.sent_at is not None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_firestore_failure_schedules_retry_without_marking_sent(mocker):
    row = make_row(attempts=0)
    session = make_session([row])
    mocker.patch("app.services.projection_service.firestore_client.get_client")
    mocker.patch("app.services.projection_service._doc_ref", return_value=MagicMock())
    mocker.patch(
        "app.services.projection_service._write_with_version_guard",
        AsyncMock(side_effect=RuntimeError("firestore unavailable")),
    )

    sent = await projection_service.drain_pending(session)

    assert sent == 0
    assert row.status == PENDING  # not SENT — Postgres state is unaffected
    assert row.attempts == 1
    assert row.next_attempt_at > datetime.now(UTC)
    assert "firestore unavailable" in row.last_error


@pytest.mark.asyncio
async def test_row_is_marked_failed_after_max_attempts(mocker):
    row = make_row(attempts=MAX_ATTEMPTS - 1)
    session = make_session([row])
    mocker.patch("app.services.projection_service.firestore_client.get_client")
    mocker.patch("app.services.projection_service._doc_ref", return_value=MagicMock())
    mocker.patch(
        "app.services.projection_service._write_with_version_guard",
        AsyncMock(side_effect=RuntimeError("permanent failure")),
    )

    await projection_service.drain_pending(session)

    assert row.status == FAILED


@pytest.mark.asyncio
async def test_stale_version_write_still_marks_row_sent(mocker, fake_doc_ref):
    """The row represents 'this event was delivered', not 'this event won the
    version race' — a dropped-as-stale write is still a successfully drained
    row, just a no-op against Firestore."""
    row = make_row()
    session = make_session([row])
    mocker.patch("app.services.projection_service.firestore_client.get_client")
    mocker.patch("app.services.projection_service._doc_ref", return_value=fake_doc_ref)
    mocker.patch(
        "app.services.projection_service._write_with_version_guard", AsyncMock(return_value=False)
    )

    sent = await projection_service.drain_pending(session)

    assert sent == 1
    assert row.status == SENT
