"""Unit tests for BillingService.handle_webhook — the Razorpay webhook
application logic (signature verification is tested separately in
test_razorpay_signature.py; this file assumes the signature already passed,
exactly as the router does by the time it calls this method).
"""

import json
import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.db.models.outbox import ProjectionOutbox
from app.db.models.payment import BillingEvent
from app.db.models.subscription import Subscription
from app.services.billing_service import BillingService

TENANT_ID = uuid.uuid4()


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    results = []
    for value in execute_results:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)
    session.execute = AsyncMock(side_effect=results)
    return session


def added_instances(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


def make_subscription(**overrides) -> Subscription:
    defaults = {
        "tenant_id": TENANT_ID,
        "plan_id": uuid.uuid4(),
        "status": "created",
        "provider": "razorpay",
        "current_period_end": None,
        "provider_event_at": None,
    }
    defaults.update(overrides)
    sub = Subscription(**defaults)
    sub.id = uuid.uuid4()
    return sub


def subscription_envelope(
    event_type: str,
    *,
    tenant_id: uuid.UUID | None = TENANT_ID,
    subscription_ref: str = "sub_test123",
    created_at: int | None = None,
    current_end: int | None = None,
) -> bytes:
    entity = {"id": subscription_ref, "customer_id": "cust_test1", "cancel_at_cycle_end": 0}
    if tenant_id is not None:
        entity["notes"] = {"tenant_id": str(tenant_id)}
    if current_end is not None:
        entity["current_end"] = current_end
    envelope = {
        "entity": "event",
        "event": event_type,
        "contains": ["subscription"],
        "payload": {"subscription": {"entity": entity}},
        "created_at": created_at if created_at is not None else int(time.time()),
    }
    return json.dumps(envelope).encode()


def payment_envelope(
    event_type: str, *, tenant_id: uuid.UUID | None = TENANT_ID, payment_id: str = "pay_abc123"
) -> bytes:
    entity = {"id": payment_id, "amount": 249900, "method": "upi"}
    if tenant_id is not None:
        entity["notes"] = {"tenant_id": str(tenant_id)}
    envelope = {
        "entity": "event",
        "event": event_type,
        "contains": ["payment"],
        "payload": {"payment": {"entity": entity}},
        "created_at": int(time.time()),
    }
    return json.dumps(envelope).encode()


@pytest.fixture(autouse=True)
def _mock_rls(mocker):
    mocker.patch("app.services.billing_service.rls.set_tenant_context", AsyncMock())


# --- malformed / structural -------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_raises_400():
    session = make_session([])
    with pytest.raises(HTTPException) as exc_info:
        await BillingService(session=session).handle_webhook(b"not json", {})
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "WEBHOOK_MALFORMED"


@pytest.mark.asyncio
async def test_missing_event_type_raises_400():
    session = make_session([])
    body = json.dumps({"created_at": int(time.time()), "payload": {}}).encode()
    with pytest.raises(HTTPException) as exc_info:
        await BillingService(session=session).handle_webhook(body, {})
    assert exc_info.value.status_code == 400


# --- idempotency --------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_delivery_is_reported_and_not_reapplied():
    """The unique provider_event_id constraint is the idempotency arbiter —
    simulated here as the flush() raising IntegrityError."""
    session = make_session([])
    session.flush = AsyncMock(side_effect=IntegrityError("dup", {}, Exception()))

    result = await BillingService(session=session).handle_webhook(
        subscription_envelope("subscription.activated"), {}
    )

    assert result == {"status": "duplicate"}
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


# --- tenant resolution ----------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_unresolved_is_stored_but_not_applied():
    """No notes.tenant_id (a webhook for something this service never
    created via checkout) must still be durably recorded, but cannot update
    any Subscription row since there is nothing to scope it to."""
    session = make_session([])  # only the BillingEvent flush — no further query

    result = await BillingService(session=session).handle_webhook(
        subscription_envelope("subscription.activated", tenant_id=None), {}
    )

    assert result == {"status": "received", "tenant_resolved": False}
    events = added_instances(session, BillingEvent)
    assert events[0].tenant_id is None
    session.commit.assert_awaited_once()


# --- subscription event application ----------------------------------------


@pytest.mark.asyncio
async def test_subscription_activated_updates_status_and_enqueues_projection():
    subscription = make_subscription(status="created")
    session = make_session([subscription])  # select(Subscription)

    result = await BillingService(session=session).handle_webhook(
        subscription_envelope("subscription.activated"), {}
    )

    assert result == {"status": "processed"}
    assert subscription.status == "active"
    assert subscription.provider_subscription_ref == "sub_test123"
    outbox_rows = added_instances(session, ProjectionOutbox)
    assert outbox_rows[0].aggregate_type == "subscription"
    assert outbox_rows[0].payload["status"] == "active"


@pytest.mark.asyncio
async def test_subscription_updated_event_does_not_change_status():
    """subscription.updated refreshes period fields but is not itself a
    status transition."""
    subscription = make_subscription(status="active")
    session = make_session([subscription])

    await BillingService(session=session).handle_webhook(
        subscription_envelope("subscription.updated"), {}
    )

    assert subscription.status == "active"


@pytest.mark.asyncio
async def test_stale_subscription_event_is_dropped():
    """Razorpay delivers at-least-once with no ordering guarantee — a retried
    older event must not clobber a status already advanced by a newer one."""
    now = int(time.time())
    subscription = make_subscription(
        status="active", provider_event_at=datetime.fromtimestamp(now, tz=UTC)
    )
    session = make_session([subscription])

    await BillingService(session=session).handle_webhook(
        subscription_envelope("subscription.cancelled", created_at=now - 3600), {}
    )

    assert subscription.status == "active"  # NOT overwritten to "canceled"
    assert not added_instances(session, ProjectionOutbox)


@pytest.mark.asyncio
async def test_webhook_for_unknown_subscription_does_not_crash():
    """A webhook for a subscription this service never created (no local row
    for the tenant) is logged and otherwise a no-op, not an error."""
    session = make_session([None])  # select(Subscription) -> no row

    result = await BillingService(session=session).handle_webhook(
        subscription_envelope("subscription.activated"), {}
    )

    assert result == {"status": "processed"}
    assert not added_instances(session, ProjectionOutbox)


# --- payment event application ----------------------------------------------


@pytest.mark.asyncio
async def test_payment_captured_enqueues_a_payment_projection():
    session = make_session([])  # no Subscription lookup for payment.* events

    result = await BillingService(session=session).handle_webhook(
        payment_envelope("payment.captured"), {}
    )

    assert result == {"status": "processed"}
    outbox_rows = added_instances(session, ProjectionOutbox)
    assert outbox_rows[0].aggregate_type == "payment"
    assert outbox_rows[0].payload["status"] == "captured"
    assert outbox_rows[0].payload["provider_payment_ref"] == "pay_abc123"
