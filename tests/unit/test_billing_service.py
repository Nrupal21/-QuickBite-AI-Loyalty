"""Unit tests for BillingService.cancel_subscription / reactivate_subscription.

`rls.tenant_context` is mocked as a no-op for the finalize-task tests below —
same convention test_review_sync_service.py uses for the same reason (a
worker session with no execute-result slots reserved for the SET/RESET
statements `tenant_context` issues)."""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.db.models.audit import AuditLog
from app.db.models.subscription import Subscription
from app.services.billing_service import BillingService

TENANT_ID = uuid.uuid4()


@asynccontextmanager
async def _noop_tenant_context(session, tenant_id):  # noqa: ARG001
    yield


@pytest.fixture(autouse=True)
def no_tenant_context(mocker):
    # tasks.py imports `rls` lazily inside the function (not at module scope,
    # per this file's own existing convention) — patch the owning module's
    # attribute directly so the lazy `from app.db import rls` picks it up.
    mocker.patch("app.db.rls.tenant_context", _noop_tenant_context)


def make_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock(side_effect=[make_result(v) for v in execute_results])
    return session


def added(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


def make_subscription(**overrides) -> Subscription:
    plan_id = uuid.uuid4()
    defaults = {
        "tenant_id": TENANT_ID,
        "plan_id": plan_id,
        "status": "active",
        "provider": "razorpay",
        "provider_subscription_ref": "sub_test123",
        "current_period_end": datetime.now(timezone.utc) + timedelta(days=10),
        "cancel_at_period_end": False,
        "trial_ends_at": None,
    }
    defaults.update(overrides)
    sub = Subscription(**defaults)
    sub.id = uuid.uuid4()
    return sub


# --- cancel_subscription -----------------------------------------------------


def make_admin() -> MagicMock:
    admin = MagicMock()
    admin.id = uuid.uuid4()
    return admin


@pytest.mark.asyncio
async def test_cancel_sets_flag_schedules_task_and_audit_logs(mocker):
    sub = make_subscription(cancel_at_period_end=False)
    # Three execute calls:
    # 1. get subscription in cancel_subscription
    # 2. get subscription in get_subscription_status
    # 3. get plan in get_subscription_status
    session = make_session([sub, sub, None])
    admin = make_admin()
    apply_async = mocker.patch(
        "app.services.billing_service.finalize_subscription_cancellation.apply_async",
        MagicMock(),
    )

    response = await BillingService(session=session).cancel_subscription(TENANT_ID, admin)

    assert response.cancel_at_period_end is True
    assert sub.cancel_at_period_end is True
    apply_async.assert_called_once_with(
        args=[str(sub.id), str(TENANT_ID)], eta=sub.current_period_end
    )
    entry = added(session, AuditLog)[-1]
    assert entry.action == "billing.subscription_canceled"
    assert entry.tenant_id == TENANT_ID
    assert entry.user_id == admin.id


@pytest.mark.asyncio
async def test_cancel_already_canceled_returns_409():
    sub = make_subscription(cancel_at_period_end=True)
    session = make_session([sub])
    admin = make_admin()

    with pytest.raises(HTTPException) as exc_info:
        await BillingService(session=session).cancel_subscription(TENANT_ID, admin)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "SUBSCRIPTION_ALREADY_CANCELED"


@pytest.mark.asyncio
async def test_cancel_no_subscription_returns_404():
    session = make_session([None])
    admin = make_admin()

    with pytest.raises(HTTPException) as exc_info:
        await BillingService(session=session).cancel_subscription(TENANT_ID, admin)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "SUBSCRIPTION_NOT_FOUND"


# --- finalize_subscription_cancellation (Celery task) -------------------------


@pytest.mark.asyncio
async def test_finalize_calls_razorpay_when_still_pending_cancel(mocker):
    sub = make_subscription(cancel_at_period_end=True)
    session = make_session([sub])
    client = MagicMock()
    mocker.patch("app.workers.tasks._get_razorpay_client", MagicMock(return_value=client))

    from app.workers.tasks import _finalize_subscription_cancellation_async

    # This tests the async helper directly, passing the mocked session in —
    # it does not exercise async_session_factory's own `async with` wiring,
    # which is a thin, untested-elsewhere-either wrapper the Celery task
    # function itself owns (see finalize_subscription_cancellation's body).
    await _finalize_subscription_cancellation_async(session, str(sub.id), str(TENANT_ID))

    # 0, not 1: this fires AT current_period_end, so the cycle being waited
    # out is already over — "cancel at cycle end" here would target the
    # *next* cycle instead. 0 cancels immediately, which is correct now.
    client.subscription.cancel.assert_called_once_with(
        sub.provider_subscription_ref, data={"cancel_at_cycle_end": 0}
    )
    assert sub.status == "canceled"


@pytest.mark.asyncio
async def test_finalize_noops_when_reactivated_before_it_ran():
    sub = make_subscription(cancel_at_period_end=False)  # reactivated
    session = make_session([sub])

    from app.workers.tasks import _finalize_subscription_cancellation_async

    # Must not raise, must not need a razorpay client at all.
    await _finalize_subscription_cancellation_async(session, str(sub.id), str(TENANT_ID))


# --- reactivate_subscription --------------------------------------------------


@pytest.mark.asyncio
async def test_reactivate_clears_flag_and_audit_logs():
    sub = make_subscription(cancel_at_period_end=True)
    session = make_session([sub, sub, None])
    admin = MagicMock()
    admin.id = uuid.uuid4()

    response = await BillingService(session=session).reactivate_subscription(TENANT_ID, admin)

    assert response.cancel_at_period_end is False
    assert sub.cancel_at_period_end is False
    entry = added(session, AuditLog)[-1]
    assert entry.action == "billing.subscription_reactivated"


@pytest.mark.asyncio
async def test_reactivate_not_canceled_returns_409():
    sub = make_subscription(cancel_at_period_end=False)
    session = make_session([sub])
    admin = MagicMock()
    admin.id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        await BillingService(session=session).reactivate_subscription(TENANT_ID, admin)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "SUBSCRIPTION_NOT_CANCELED"


@pytest.mark.asyncio
async def test_reactivate_already_ended_returns_409():
    sub = make_subscription(
        cancel_at_period_end=True,
        current_period_end=datetime.now(timezone.utc) - timedelta(days=1),
    )
    session = make_session([sub])
    admin = MagicMock()
    admin.id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        await BillingService(session=session).reactivate_subscription(TENANT_ID, admin)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "SUBSCRIPTION_ALREADY_ENDED"


@pytest.mark.asyncio
async def test_reactivate_no_subscription_returns_404():
    session = make_session([None])
    admin = MagicMock()
    admin.id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        await BillingService(session=session).reactivate_subscription(TENANT_ID, admin)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "SUBSCRIPTION_NOT_FOUND"
