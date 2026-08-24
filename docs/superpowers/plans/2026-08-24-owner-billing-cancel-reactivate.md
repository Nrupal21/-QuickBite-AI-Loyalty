# Owner Billing Cancel/Reactivate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an Owner cancel their subscription (access continues until the current billing period ends) and undo that cancellation before it takes effect, closing the one real gap in the already-built `billing.html`/`billing.js` WIP.

**Architecture:** Two new `BillingService` methods + two new routes, both `require_role(OWNER)`-gated. Cancellation never calls Razorpay directly — Razorpay has no API to reverse a sent cancellation, so the real Razorpay call is deferred to a new Celery task scheduled for the subscription's `current_period_end`, which re-checks the local `cancel_at_period_end` flag before acting. Reactivate only ever flips that local flag back — it never touches Razorpay or the scheduled task, because the task's own check makes that safe.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, Celery 5.3, razorpay-python (sync SDK), Jinja2, Tailwind CDN + `qb-glass` CSS (existing, unchanged).

**Spec:** `docs/superpowers/specs/2026-08-24-owner-billing-cancel-reactivate-design.md`

## Global Constraints

- Every mutating endpoint: `require_role(RoleLevel.OWNER)`, `@limiter.limit("10/hour")` (matching `/billing/checkout`'s own limit), an `AuditLog` row, structlog with no PII.
- Celery tasks: `@celery_app.task(bind=True, max_retries=3)`, JSON-serializable args only (pass `subscription_id` as `str`, not `uuid.UUID`), idempotent (the finalize task must be safe to run twice or not at all).
- Never call the synchronous `razorpay` SDK directly from an async FastAPI route without `anyio.to_thread.run_sync` (blocks the shared event loop) — but INSIDE a Celery task's own `asyncio.run()`-wrapped `_run()` closure, this codebase's existing tasks (`drain_projection_outbox`, `batch_generate_ai_responses`) call blocking-ish code directly with no `anyio.to_thread` wrapper, because each task owns its own throwaway event loop. Follow that established pattern, not the FastAPI-route pattern, for the new Celery task.
- `pytest` needs `-p no:langsmith_plugin` to collect in this repo.
- This worktree must branch from `sdd/super-admin-manager-dashboards` (already pushed to `origin`), NOT from `feature/marketing-01-whatsapp-direct` directly — the earlier `require_role(OWNER)` fix on `POST /billing/checkout` only exists on that branch, and this plan's new routes must be consistent with it, not reintroduce the gap.

---

### Task 1: Cancel — service method, Celery task, route

**Files:**
- Modify: `app/services/billing_service.py`
- Modify: `app/workers/tasks.py`
- Modify: `app/api/v1/routers/billing.py`
- Test: `tests/unit/test_billing_service.py` (new file)

**Interfaces:**
- Consumes: `Subscription` model (existing), `AuditLog` (existing), `SubscriptionStatusResponse` (existing schema, reused as the return type), `celery_app` (existing, from `app.workers.celery_app`).
- Produces: `BillingService.cancel_subscription(tenant_id: uuid.UUID) -> SubscriptionStatusResponse`, `finalize_subscription_cancellation(subscription_id: str) -> None` (Celery task, importable as `app.workers.tasks.finalize_subscription_cancellation`) — Task 2 does not consume these directly, but Task 3 (frontend) consumes the route's response shape.

- [ ] **Step 1: Write the failing service tests**

Create `tests/unit/test_billing_service.py`. This is the first dedicated test file for `BillingService` (only `test_billing_webhook.py` existed before, covering the webhook handler) — mirror `tests/unit/test_admin_service.py`'s mocking conventions exactly (same house style: `make_session`/`make_result` helpers, `added()` helper to inspect `session.add` calls).

```python
"""Unit tests for BillingService.cancel_subscription / reactivate_subscription."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.db.models.audit import AuditLog
from app.db.models.subscription import Subscription
from app.services.billing_service import BillingService

TENANT_ID = uuid.uuid4()


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
    defaults = {
        "tenant_id": TENANT_ID,
        "plan_id": uuid.uuid4(),
        "status": "active",
        "provider": "razorpay",
        "provider_subscription_ref": "sub_test123",
        "current_period_end": datetime.now(timezone.utc) + timedelta(days=10),
        "cancel_at_period_end": False,
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
    session = make_session([sub])
    admin = make_admin()
    apply_async = mocker.patch(
        "app.services.billing_service.finalize_subscription_cancellation.apply_async",
        MagicMock(),
    )

    response = await BillingService(session=session).cancel_subscription(TENANT_ID, admin)

    assert response.cancel_at_period_end is True
    assert sub.cancel_at_period_end is True
    apply_async.assert_called_once_with(
        args=[str(sub.id)], eta=sub.current_period_end
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
    await _finalize_subscription_cancellation_async(session, str(sub.id))

    client.subscription.cancel.assert_called_once_with(
        sub.provider_subscription_ref, data={"cancel_at_cycle_end": 1}
    )


@pytest.mark.asyncio
async def test_finalize_noops_when_reactivated_before_it_ran():
    sub = make_subscription(cancel_at_period_end=False)  # reactivated
    session = make_session([sub])

    from app.workers.tasks import _finalize_subscription_cancellation_async

    # Must not raise, must not need a razorpay client at all.
    await _finalize_subscription_cancellation_async(session, str(sub.id))
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_billing_service.py -p no:langsmith_plugin -v`
Expected: FAIL — `cancel_subscription`, `finalize_subscription_cancellation`, `_finalize_subscription_cancellation_async`, `_get_razorpay_client` don't exist yet.

- [ ] **Step 3: Read the existing checkout method and one existing Celery task first**

Before writing code, read `app/services/billing_service.py`'s `create_checkout_order` method in full (for the `_PLAN_NOT_FOUND`-style error-constant convention and the `_upsert_subscription`/`logger.info` shape to match), and `app/workers/tasks.py`'s `drain_projection_outbox` task in full (for the exact `asyncio.run(_run())` + local-import + `# noqa: PLC0415` shape to match). Do not guess these patterns — copy their exact style.

- [ ] **Step 4: Implement `BillingService.cancel_subscription`**

Add near the top of `billing_service.py`, alongside the existing `_PLAN_NOT_FOUND`/`_PLAN_NOT_PROVISIONED` constants:

```python
_SUBSCRIPTION_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail={
        "error": {
            "code": "SUBSCRIPTION_NOT_FOUND",
            "message": "This tenant has no subscription to cancel.",
        }
    },
)

_SUBSCRIPTION_ALREADY_CANCELED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail={
        "error": {
            "code": "SUBSCRIPTION_ALREADY_CANCELED",
            "message": "This subscription is already scheduled to cancel.",
        }
    },
)

_SUBSCRIPTION_NOT_CANCELED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail={
        "error": {
            "code": "SUBSCRIPTION_NOT_CANCELED",
            "message": "This subscription isn't scheduled to cancel.",
        }
    },
)

_SUBSCRIPTION_ALREADY_ENDED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail={
        "error": {
            "code": "SUBSCRIPTION_ALREADY_ENDED",
            "message": "This subscription's billing period has already ended.",
        }
    },
)
```

Add the import (near the top, with the other `app.workers` or lazy-import spots — check whether other services in this codebase import Celery tasks at module scope or lazily; `admin_service.py`'s `trigger_gmb_sync` imports `sync_gmb_tenant` lazily inside the method specifically to avoid a service/task import cycle — follow that exact precedent). The method takes `admin: User` (mirroring `override_subscription(self, tenant_id, payload, admin)`'s existing shape in `admin_service.py`) so the audit-log entry can record who cancelled — add `from app.db.models.user import User` to `billing_service.py`'s imports if not already present (check first — `create_checkout_order` takes a `Tenant`, not a `User`, so this import may be new to this file):

```python
    async def cancel_subscription(
        self, tenant_id: uuid.UUID, admin: User
    ) -> SubscriptionStatusResponse:
        # Local import — avoids a service/task import cycle, same reason
        # admin_service.trigger_gmb_sync imports sync_gmb_tenant lazily.
        from app.workers.tasks import finalize_subscription_cancellation  # noqa: PLC0415

        result = await self.session.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            raise _SUBSCRIPTION_NOT_FOUND
        if subscription.cancel_at_period_end:
            raise _SUBSCRIPTION_ALREADY_CANCELED

        subscription.cancel_at_period_end = True
        await self.session.commit()

        finalize_subscription_cancellation.apply_async(
            args=[str(subscription.id)], eta=subscription.current_period_end
        )

        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                user_id=admin.id,
                action="billing.subscription_canceled",
                resource_type="subscription",
                resource_id=subscription.id,
                event_metadata=None,
            )
        )
        await self.session.commit()

        logger.info(
            "billing.subscription.cancel_scheduled",
            tenant_id=str(tenant_id),
            subscription_id=str(subscription.id),
        )
        return await self.get_subscription_status(tenant_id)
```

- [ ] **Step 5: Implement `finalize_subscription_cancellation` in `app/workers/tasks.py`**

Add a lazy Razorpay-client getter near the top of the file (mirroring `billing_service.py`'s own `_get_client()` — do NOT import it from there directly, since `billing_service.py` imports `app.workers.tasks` and a reverse import would cycle; duplicate the small lazy-client pattern instead):

```python
_razorpay_client: Any = None


def _get_razorpay_client() -> Any:
    global _razorpay_client  # noqa: PLW0603
    if _razorpay_client is not None:
        return _razorpay_client
    from app.core.config import settings  # noqa: PLC0415

    if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
        return None
    import razorpay  # noqa: PLC0415

    _razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    return _razorpay_client
```

Add `from typing import Any` to the file's imports if not already present.

Add the async helper and the Celery task itself, following `drain_projection_outbox`'s exact shape:

```python
async def _finalize_subscription_cancellation_async(session, subscription_id: str) -> None:
    from sqlalchemy import select  # noqa: PLC0415

    from app.db.models.subscription import Subscription  # noqa: PLC0415

    result = await session.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()
    if subscription is None or not subscription.cancel_at_period_end:
        # Deleted, or reactivated before this task fired — no-op, matches
        # the idempotent-task convention every other task here follows.
        logger.info(
            "billing.subscription.finalize_skipped", subscription_id=subscription_id
        )
        return

    client = _get_razorpay_client()
    if client is None:
        logger.warning(
            "billing.subscription.finalize_no_client", subscription_id=subscription_id
        )
        return

    client.subscription.cancel(
        subscription.provider_subscription_ref, data={"cancel_at_cycle_end": 1}
    )
    logger.info(
        "billing.subscription.finalized", subscription_id=subscription_id
    )


@celery_app.task(bind=True, max_retries=3)
def finalize_subscription_cancellation(self, subscription_id: str) -> None:
    """Doc 3 / this session's billing spec: the real Razorpay cancel call is
    deferred to here, scheduled for the subscription's current_period_end,
    because Razorpay has no API to reverse a sent cancellation. Re-checks
    cancel_at_period_end before acting so a reactivation in the meantime
    makes this a safe no-op."""
    from app.db.base import async_session_factory  # noqa: PLC0415

    async def _run() -> None:
        async with async_session_factory() as session:
            await _finalize_subscription_cancellation_async(session, subscription_id)

    asyncio.run(_run())
```

- [ ] **Step 6: Add the route**

In `app/api/v1/routers/billing.py`, extend the `from app.schemas.billing import (...)` block if a new schema is needed (it isn't — reusing `SubscriptionStatusResponse`), and add:

```python
@router.post("/cancel", response_model=SubscriptionStatusResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/hour")
async def cancel_subscription(
    request: Request,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> SubscriptionStatusResponse:
    return await BillingService(session=session).cancel_subscription(
        current_user.tenant_id, current_user
    )
```

`require_role` and `RoleLevel` are already imported in this file from the earlier `POST /billing/checkout` fix (this worktree branches from `sdd/super-admin-manager-dashboards`, which already has that fix — confirm the import line is present before adding a duplicate).

- [ ] **Step 7: Run to verify Step 1's cancel tests pass**

Run: `pytest tests/unit/test_billing_service.py -p no:langsmith_plugin -v`
Expected: PASS for the `cancel_subscription` and `finalize_subscription_cancellation` tests (the reactivate tests from Task 2 aren't written yet).

- [ ] **Step 8: Run the full suite and commit**

Run: `pytest tests/unit/ -p no:langsmith_plugin -q`
Expected: PASS, no regressions.

```bash
git add app/services/billing_service.py app/workers/tasks.py app/api/v1/routers/billing.py tests/unit/test_billing_service.py
git commit -m "feat(billing): add POST /billing/cancel — deferred cancellation via Celery

Never calls Razorpay directly (no API exists to reverse a sent
cancellation). Sets cancel_at_period_end locally and schedules
finalize_subscription_cancellation for the period's actual end,
which re-checks the flag before making the real Razorpay call."
```

---

### Task 2: Reactivate — service method, route

**Files:**
- Modify: `app/services/billing_service.py`
- Modify: `app/api/v1/routers/billing.py`
- Test: `tests/unit/test_billing_service.py`

**Interfaces:**
- Consumes: `Subscription`, `AuditLog`, `SubscriptionStatusResponse` (existing), the `_SUBSCRIPTION_NOT_FOUND`/`_SUBSCRIPTION_NOT_CANCELED`/`_SUBSCRIPTION_ALREADY_ENDED` constants Task 1 added.
- Produces: `BillingService.reactivate_subscription(tenant_id: uuid.UUID, admin: User) -> SubscriptionStatusResponse`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_billing_service.py`:

```python
# --- reactivate_subscription --------------------------------------------------


@pytest.mark.asyncio
async def test_reactivate_clears_flag_and_audit_logs():
    sub = make_subscription(cancel_at_period_end=True)
    session = make_session([sub])
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_billing_service.py -k reactivate -p no:langsmith_plugin -v`
Expected: FAIL — method doesn't exist.

- [ ] **Step 3: Implement**

Add to `billing_service.py`, alongside `cancel_subscription`:

```python
    async def reactivate_subscription(
        self, tenant_id: uuid.UUID, admin: User
    ) -> SubscriptionStatusResponse:
        result = await self.session.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            raise _SUBSCRIPTION_NOT_FOUND
        if not subscription.cancel_at_period_end:
            raise _SUBSCRIPTION_NOT_CANCELED
        if subscription.current_period_end < datetime.now(UTC):
            raise _SUBSCRIPTION_ALREADY_ENDED

        subscription.cancel_at_period_end = False

        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                user_id=admin.id,
                action="billing.subscription_reactivated",
                resource_type="subscription",
                resource_id=subscription.id,
                event_metadata=None,
            )
        )
        await self.session.commit()

        logger.info(
            "billing.subscription.reactivated",
            tenant_id=str(tenant_id),
            subscription_id=str(subscription.id),
        )
        return await self.get_subscription_status(tenant_id)
```

`datetime` and `UTC` are already imported in `billing_service.py` (`from datetime import UTC, datetime`, per the file's existing header) — confirm before adding a duplicate import.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_billing_service.py -p no:langsmith_plugin -v`
Expected: PASS, every test in the file.

- [ ] **Step 5: Add the route**

```python
@router.post("/reactivate", response_model=SubscriptionStatusResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/hour")
async def reactivate_subscription(
    request: Request,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> SubscriptionStatusResponse:
    return await BillingService(session=session).reactivate_subscription(
        current_user.tenant_id, current_user
    )
```

- [ ] **Step 6: Run the full suite and commit**

Run: `pytest tests/unit/ -p no:langsmith_plugin -q`
Expected: PASS, no regressions.

```bash
git add app/services/billing_service.py app/api/v1/routers/billing.py tests/unit/test_billing_service.py
git commit -m "feat(billing): add POST /billing/reactivate — undo a pending cancellation

Only ever flips cancel_at_period_end back locally; never calls
Razorpay, since the deferred-cancellation design means Razorpay was
never told about the cancellation unless the period actually ended."
```

---

### Task 3: Frontend — cancel/reactivate button, wired

**Files:**
- Create: `app/templates/dashboard/billing.html` (doesn't exist in this worktree yet — untracked WIP in the main repo)
- Create: `static/js/billing.js` (same — untracked WIP in the main repo)

**Interfaces:**
- Consumes: `POST /billing/cancel`, `POST /billing/reactivate` (Tasks 1-2), both returning `SubscriptionStatusResponse` (`status`, `plan_name`, `provider`, `current_period_end`, `trial_ends_at`, `cancel_at_period_end`).
- Produces: nothing consumed by another task — this is the last task in this plan.

- [ ] **Step 1: Create `static/js/billing.js`**

This file does not exist in this worktree (it's untracked WIP in the main repo checkout, never committed). Create it with this exact content — this is the real, already-working file from the main repo (subscription display, plan cards, checkout — all unchanged) with the cancel/reactivate additions appended at the end:

```javascript
/* QuickBite AI + Loyalty — Billing page.
 *
 * Same session/auth contract as dashboard.js — see that file's header.
 * GET /api/v1/billing/subscription and GET /api/v1/billing/plans are both
 * already-shipped endpoints (no new backend for this page). Checkout hands
 * off to Razorpay's own hosted subscription page (`short_url` from
 * POST /billing/checkout) rather than embedding Razorpay's Checkout.js
 * widget — this app has no payment-verification callback endpoint for the
 * embedded flow, and the hosted page is what create_checkout_order already
 * returns a URL for.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';

  var STATUS_LABEL = {
    trialing: 'Trial', active: 'Active', past_due: 'Past due',
    canceled: 'Canceled', paused: 'Paused', none: 'No plan',
  };

  function readSession() {
    try {
      var raw = sessionStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  var session = readSession();
  if (!session || !session.access_token) {
    window.location.replace('/login');
    return;
  }

  function showError(message) {
    var box = document.getElementById('billing-error');
    if (!box) return;
    box.textContent = message;
    box.classList.remove('hidden');
  }

  function apiFetch(path, options) {
    return fetch(API_BASE + path, Object.assign({}, options, {
      headers: Object.assign(
        { Authorization: 'Bearer ' + session.access_token },
        (options && options.headers) || {}
      ),
    })).then(function (response) {
      if (response.status === 401) {
        sessionStorage.removeItem(SESSION_KEY);
        window.location.replace('/login');
        throw new Error('unauthorized');
      }
      return response.json().then(function (data) {
        if (!response.ok) {
          var err = (data && data.detail && data.detail.error) || {};
          throw new Error(err.message || 'Something went wrong.');
        }
        return data;
      });
    });
  }

  // ---------- profile chip (same as dashboard.js) ----------

  var initialsEls = document.querySelectorAll('[data-user-initials]');
  var roleLabelEl = document.querySelector('[data-user-role-label]');
  var roleBadgeEl = document.querySelector('[data-user-role-badge]');
  if (session.role) {
    var chipLabel = String(session.role).replace(/_/g, ' ');
    if (roleLabelEl) roleLabelEl.textContent = chipLabel.charAt(0) + chipLabel.slice(1).toLowerCase();
    if (roleBadgeEl) roleBadgeEl.textContent = chipLabel;
    initialsEls.forEach(function (el) {
      el.textContent = chipLabel.charAt(0);
    });
  }

  // ---------- current subscription ----------

  function formatDate(iso) {
    return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  var currentPlanName = null;
  var currentSub = null;

  function setSubField(el, text) {
    if (!el) return;
    el.classList.remove('qb-skel');
    el.removeAttribute('data-sub-loading');
    el.textContent = text;
  }

  function renderSubscription(sub) {
    currentPlanName = sub.plan_name;
    currentSub = sub;

    var statusWrap = document.querySelector('[data-sub-status]');
    if (statusWrap) {
      statusWrap.textContent = STATUS_LABEL[sub.status] || sub.status;
      statusWrap.className = 'qb-sub-status qb-sub-status--' + sub.status;
    }

    setSubField(document.querySelector('[data-sub-plan-name]'), sub.plan_name || 'No active plan');

    var metaEl = document.querySelector('[data-sub-meta]');
    if (sub.status === 'trialing' && sub.trial_ends_at) {
      setSubField(metaEl, 'Trial ends ' + formatDate(sub.trial_ends_at));
    } else if (sub.current_period_end) {
      setSubField(metaEl, (sub.cancel_at_period_end ? 'Ends ' : 'Renews ') + formatDate(sub.current_period_end));
    } else {
      setSubField(metaEl, 'Choose a plan below to get started.');
    }

    var cancelNoticeEl = document.querySelector('[data-sub-cancel-notice]');
    if (cancelNoticeEl) cancelNoticeEl.hidden = !sub.cancel_at_period_end;

    renderCancelAction(sub);
  }

  function loadSubscription() {
    return apiFetch('/billing/subscription')
      .then(renderSubscription)
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  // ---------- cancel / reactivate ----------
  // Only Owner reaches this page's mutating actions at all (require_role
  // on both routes); no client-side role gate needed here the way
  // google-profile.js/settings.js gate Manager, since this whole page's
  // only mutating actions (checkout, cancel, reactivate) are all
  // Owner-only and Manager/Staff simply get a 403 if they somehow reach
  // this page and click — matching this page's existing convention of
  // not hiding the plan grid from non-Owner roles either.

  function renderCancelAction(sub) {
    var mount = document.querySelector('[data-cancel-action]');
    if (!mount) return;

    var hasActiveOrPastDue = sub.status === 'active' || sub.status === 'past_due';

    if (hasActiveOrPastDue && !sub.cancel_at_period_end) {
      mount.innerHTML = '<button type="button" class="qb-plan-cta" data-cancel-subscription>Cancel subscription</button>';
    } else if (sub.cancel_at_period_end) {
      mount.innerHTML = '<button type="button" class="qb-plan-cta" data-reactivate-subscription>Keep my plan</button>';
    } else {
      mount.innerHTML = '';
    }
  }

  document.addEventListener('click', function (event) {
    var cancelBtn = event.target.closest('[data-cancel-subscription]');
    if (cancelBtn) {
      var endDate = currentSub && currentSub.current_period_end
        ? formatDate(currentSub.current_period_end)
        : 'the end of your current period';
      if (!window.confirm('Cancel your subscription? You\'ll keep access until ' + endDate + '.')) return;

      cancelBtn.disabled = true;
      cancelBtn.textContent = 'Cancelling…';
      apiFetch('/billing/cancel', { method: 'POST' })
        .then(renderSubscription)
        .catch(function (error) {
          cancelBtn.disabled = false;
          cancelBtn.textContent = 'Cancel subscription';
          if (error.message !== 'unauthorized') showError(error.message);
        });
      return;
    }

    var reactivateBtn = event.target.closest('[data-reactivate-subscription]');
    if (reactivateBtn) {
      reactivateBtn.disabled = true;
      reactivateBtn.textContent = 'Restoring…';
      apiFetch('/billing/reactivate', { method: 'POST' })
        .then(renderSubscription)
        .catch(function (error) {
          reactivateBtn.disabled = false;
          reactivateBtn.textContent = 'Keep my plan';
          if (error.message !== 'unauthorized') showError(error.message);
        });
    }
  });

  // ---------- plans ----------

  function formatInr(paise) {
    if (paise === 0) return 'Free';
    return '₹' + Math.round(paise / 100).toLocaleString('en-IN');
  }

  function humanizeKey(key) {
    var words = key.replace(/_/g, ' ').split(' ');
    return words.map(function (w) { return w.charAt(0).toUpperCase() + w.slice(1); }).join(' ');
  }

  function featureLines(limits) {
    return Object.keys(limits)
      .map(function (key) {
        var value = limits[key];
        var text = typeof value === 'boolean' ? humanizeKey(key) : humanizeKey(key) + ': ' + value;
        return '<li class="qb-plan-feature"><span class="material-symbols-outlined" aria-hidden="true">check_circle</span>' + text + '</li>';
      })
      .join('');
  }

  function planCardHtml(plan) {
    var isCurrent = plan.display_name === currentPlanName;
    return (
      '<div class="qb-glass qb-plan-card' + (isCurrent ? ' qb-plan-card--current' : '') + '">' +
      (isCurrent ? '<span class="qb-plan-current-badge">Current plan</span>' : '') +
      '<p class="qb-plan-name">' + plan.display_name + '</p>' +
      '<p class="qb-plan-price">' + formatInr(plan.price_monthly_inr) + (plan.price_monthly_inr > 0 ? ' <small>/ month</small>' : '') + '</p>' +
      (plan.trial_days > 0 ? '<p class="qb-plan-trial">' + plan.trial_days + '-day free trial</p>' : '') +
      '<ul class="qb-plan-features">' + featureLines(plan.feature_limits || {}) + '</ul>' +
      (isCurrent
        ? ''
        : '<button type="button" class="qb-plan-cta" data-choose-plan data-plan-id="' + plan.id + '">Choose plan</button>') +
      '</div>'
    );
  }

  function renderPlans(plans) {
    var mount = document.querySelector('[data-plan-grid]');
    if (!mount) return;
    if (!plans.length) {
      mount.innerHTML =
        '<div class="qb-empty">' +
        '<span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">credit_card</span>' +
        '<p class="text-sm font-semibold text-brand-ink">No plans available right now</p>' +
        '</div>';
      return;
    }
    mount.innerHTML = plans.map(planCardHtml).join('');
  }

  function loadPlans() {
    return apiFetch('/billing/plans')
      .then(renderPlans)
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  // Plans render relative to the current plan's name, so wait for the
  // subscription lookup before drawing the grid.
  loadSubscription().then(loadPlans);

  // ---------- checkout ----------

  document.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-choose-plan]');
    if (!btn) return;

    btn.disabled = true;
    btn.textContent = 'Starting checkout…';
    apiFetch('/billing/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_id: btn.dataset.planId }),
    })
      .then(function (data) {
        if (data.short_url) {
          window.location.href = data.short_url;
          return;
        }
        showError("Checkout isn't available for this plan yet — please contact support.");
        btn.disabled = false;
        btn.textContent = 'Choose plan';
      })
      .catch(function (error) {
        btn.disabled = false;
        btn.textContent = 'Choose plan';
        if (error.message !== 'unauthorized') showError(error.message);
      });
  });
})();
```

- [ ] **Step 2: Create `app/templates/dashboard/billing.html`**

This file also doesn't exist in this worktree yet. Copy the exact head/shell structure from `app/templates/dashboard/index.html` (Tailwind CDN + inline config with the Doc 4 brand-* tokens + `dashboard.css` link — the same verbatim-copy approach the Super Admin plan used for `admin/index.html`), with `<title>Billing — QuickBite AI + Loyalty</title>`, the "Billing" sidebar/tab-bar item marked `aria-current="page"`, and this page body:

```html
<main class="min-[900px]:ml-[240px] flex-1 min-h-screen">
  <header class="qb-glass sticky top-0 right-0 z-40 border-x-0 border-t-0 rounded-none flex justify-between items-center w-full px-5 min-[900px]:px-8 h-16">
    <h2 class="font-body font-bold text-brand-ink tracking-tight text-xl">Billing</h2>
  </header>

  <div class="p-5 min-[900px]:p-8 pb-28 min-[900px]:pb-8 max-w-7xl mx-auto space-y-6">
    <div id="billing-error" class="hidden bg-brand-danger/10 text-brand-danger text-sm font-medium px-4 py-3 rounded-lg" role="alert"></div>

    <div class="qb-glass p-6">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p class="text-xs font-bold uppercase tracking-wide text-brand-muted mb-1">Current plan</p>
          <h3 class="qb-kpi-value" data-sub-plan-name><span class="qb-skel" data-sub-loading></span></h3>
          <p class="text-sm text-brand-muted mt-1" data-sub-meta><span class="qb-skel" data-sub-loading></span></p>
          <p class="hidden mt-3 text-sm text-brand-warning font-medium" data-sub-cancel-notice>Your plan will not renew.</p>
        </div>
        <span class="qb-sub-status" data-sub-status></span>
      </div>
      <div class="mt-4" data-cancel-action></div>
    </div>

    <div>
      <h3 class="text-lg font-bold text-brand-ink mb-4">Plans</h3>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-6" data-plan-grid></div>
    </div>
  </div>
</main>

<script src="{{ url_for('static', path='js/billing.js') }}" defer></script>
</body>
</html>
```

- [ ] **Step 3: Confirm Jinja2 syntax parses**

Run: `python -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('app/templates')); env.parse(env.loader.get_source(env, 'dashboard/billing.html')[0]); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Confirm `static/js/billing.js` is syntactically valid**

Run: `node --check static/js/billing.js` (if Node is available in this environment; if not, visually confirm balanced braces/parens and report this step's limitation rather than skip verification silently)
Expected: no output (valid syntax) or `OK`.

- [ ] **Step 5: Commit**

```bash
git add app/templates/dashboard/billing.html static/js/billing.js
git commit -m "feat(billing): add cancel/reactivate UI to the billing page

Cancel prompts with the exact end-of-access date before calling
POST /billing/cancel; Keep-my-plan calls POST /billing/reactivate
with no confirmation (reversible action, not destructive). Both
re-render the subscription block from the response directly."
```

---

### Task 4: Manual verification

**Files:** none (verification only)

- [ ] **Step 1: Confirm the `/dashboard/billing` page route exists in this worktree**

This worktree branches from `sdd/super-admin-manager-dashboards`, which does NOT have a `/dashboard/billing` page route in `app/api/v1/routers/pages.py` (that route is separate uncommitted WIP in the main repo, never merged into this branch's history). Add one, matching the shell-only, no-server-side-auth-check convention every other `/dashboard/*` route in that file already uses:

```python
@router.get("/dashboard/billing", response_class=HTMLResponse)
async def billing_page(request: Request) -> HTMLResponse:
    """Current subscription status, plan grid, checkout, cancel/reactivate.
    Same no-server-side-auth-check shell as every other dashboard page."""
    return templates.TemplateResponse(request, "dashboard/billing.html")
```

Commit this as its own small commit (`git add app/api/v1/routers/pages.py && git commit -m "feat(billing): wire /dashboard/billing page route"`) — it's infrastructure the other tasks need to be reachable at all, not scope creep.

- [ ] **Step 2: Start the dev server, confirm the full suite passes**

Run: `pytest tests/unit/ -p no:langsmith_plugin -q`
Expected: PASS, no regressions.

Start the app per this repo's normal run configuration (`docker-compose up -d` per AGENTS.md, or `uvicorn app.main:app` directly in this environment, matching how prior verification in this session was done).

- [ ] **Step 3: Verify with a real or minted Owner session**

Log in as an Owner (reuse the real test account from the prior SDD session's testing if still present in the dev DB — `nrupal85@gmail.com` — or mint a token directly the way `tests/unit/test_rbac.py`'s `create_access_token` helper does, for an API-level check if no browser session is practical). Confirm:
- `GET /dashboard/billing` renders 200 with the cancel button visible when there's an active subscription.
- `POST /billing/cancel` returns 200 with `cancel_at_period_end: true`; the page (or a follow-up `GET /billing/subscription`) reflects "Ends {date}" and shows "Keep my plan" instead.
- `POST /billing/reactivate` returns 200 with `cancel_at_period_end: false`; the page reverts to showing "Renews {date}" and the "Cancel subscription" button again.
- A Manager/Staff token gets `403 INSUFFICIENT_PERMISSIONS` on both new endpoints (`require_role(OWNER)` working correctly).

Note: this dev environment has no Razorpay credentials configured (confirmed in an earlier session: `create_checkout_order` 409s with `PLAN_NOT_PROVISIONED`), so the actual Razorpay API calls inside `finalize_subscription_cancellation` cannot be exercised live — that task's logic is covered by Task 1's unit tests instead (mocked Razorpay client), and this is an accepted environment limitation, not a gap to work around.

No commit for this task — verification only. If any step fails, return to the relevant task to fix before considering this plan complete.
