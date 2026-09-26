# Owner Billing Core — Cancel/Reactivate Subscription — Design Spec

Date: 2026-08-24
Status: approved for planning

## Problem

This is sub-project 1 of 4 in the larger "subscription-related pages" request
(the other three — Invoice/Billing History, Plan Comparison/Upgrade page,
Super Admin subscription-management polish — are separate, later specs).

`static/js/billing.js` and `app/templates/dashboard/billing.html` (both
currently uncommitted WIP in the main checkout) already render subscription
status, plan cards, and checkout, and even already display a
"cancel_at_period_end" notice (`data-sub-cancel-notice`, the "Ends" vs
"Renews" label logic) — but there is **no way for an Owner to actually
trigger a cancellation**. No cancel button exists in the UI, no
`POST /billing/cancel` route exists, and `BillingService` has no
`cancel_subscription` method. The Razorpay webhook handler already *reflects*
a cancellation back into `cancel_at_period_end` when Razorpay sends one
(`_apply_subscription_event`, `billing_service.py:405`), but nothing on our
side ever causes Razorpay to send one.

## Out of scope

- The other 3 sub-projects (separate specs, separate SDD cycles).
- Immediate (non-period-end) cancellation — user confirmed cancel-at-period-end
  only, matching `Subscription.cancel_at_period_end`'s existing purpose.
- Any change to the checkout flow itself (`POST /billing/checkout` already
  works, including its recent `require_role(OWNER)` fix).
- A cancellation reason/feedback form — not requested, would be scope creep.

## Design

### Backend

**Resolved during planning (this replaces the original design, which assumed
Razorpay supports reversing a sent cancellation — it doesn't):** checked the
installed `razorpay` SDK source (`razorpay/resources/subscription.py`) and
Razorpay's own docs. `client.subscription.cancel(id, data={"cancel_at_cycle_end": 1})`
is real and reliable for *sending* a cancellation. There is no documented
"un-cancel" call — `cancel_scheduled_changes` reverses a pending *plan
update*, not a cancellation. Once Razorpay has actually received the cancel
call, it cannot be undone through their API.

**Design: defer the real Razorpay call to period-end, not cancel-time.**
`POST /billing/cancel` never calls Razorpay directly. It sets our own local
`cancel_at_period_end = True` and schedules a Celery task
(`finalize_subscription_cancellation`, ETA = `subscription.current_period_end`)
that makes the actual Razorpay call *later*, at the moment the period
genuinely ends. The task re-reads `cancel_at_period_end` from the database
before acting — if an Owner reactivated in the meantime, the flag is back to
`False` and the task no-ops. This means `POST /billing/reactivate` never
needs to revoke a Celery task or call Razorpay at all — it only ever has to
flip the local flag back, because Razorpay was never told about the
cancellation in the first place unless the period actually lapsed. This is
the same idempotent, check-before-act shape AGENTS.md §6 already mandates
for Celery tasks ("Always Use Idempotency Keys").

**`POST /billing/cancel`** — Owner-only (`require_role(RoleLevel.OWNER)`,
matching the same fix already applied to `/billing/checkout` — Manager/Staff
must not be able to cancel the tenant's plan any more than they can buy one),
rate-limited (`@limiter.limit("10/hour")`, matching checkout's own limit —
this is an equally consequential mutation).

`BillingService.cancel_subscription(tenant_id: uuid.UUID) -> SubscriptionStatusResponse`:
1. Load the tenant's `Subscription` row; 404 (`SUBSCRIPTION_NOT_FOUND`) if none.
2. 409 (`SUBSCRIPTION_ALREADY_CANCELED`) if `cancel_at_period_end` is already
   `True` — calling cancel twice is a no-op state, not a new mutation.
3. Set `subscription.cancel_at_period_end = True` locally — no Razorpay
   call here at all.
4. Schedule the finalize task: `finalize_subscription_cancellation.apply_async(args=[str(subscription.id)], eta=subscription.current_period_end)`
   (a new Celery task in `app/workers/tasks.py`, matching where every other
   task in this codebase already lives — `bind=True, max_retries=3`, JSON-
   serializable args only, per AGENTS.md §6).
5. Audit-log `action="billing.subscription_canceled"`, `tenant_id=tenant_id`,
   `user_id=current_user.id`.
6. Return the refreshed `SubscriptionStatusResponse` so the frontend can
   re-render from the response directly, no second fetch needed.

**`finalize_subscription_cancellation(subscription_id: str)`** (new Celery task):
1. Re-load the `Subscription` row by id.
2. If `cancel_at_period_end` is `False` (an Owner reactivated before this
   task fired), log and return — no-op, matches the idempotent-task
   convention.
3. Otherwise, call Razorpay: `client.subscription.cancel(subscription.provider_subscription_ref, data={"cancel_at_cycle_end": 1})`
   via the same `anyio.to_thread.run_sync` pattern `create_checkout_order`
   uses — Celery tasks in this codebase are sync functions, so check how
   `app/workers/tasks.py`'s existing tasks call async code (likely
   `asyncio.run(...)` wrapping an async helper) and follow that convention
   during implementation, not `anyio.to_thread` directly (that pattern is
   for a FastAPI async route calling a sync SDK, the inverse of a Celery
   task's context).

**`POST /billing/reactivate`** — same auth/rate-limit shape.
`BillingService.reactivate_subscription(tenant_id) -> SubscriptionStatusResponse`:
1. Load the `Subscription` row; 404 if none.
2. 409 (`SUBSCRIPTION_NOT_CANCELED`) if `cancel_at_period_end` is already `False`.
3. 409 (`SUBSCRIPTION_ALREADY_ENDED`) if `current_period_end` has already
   passed — past that point the scheduled task has already fired (or is
   about to), so this is no longer a pending cancellation to undo.
4. Set `subscription.cancel_at_period_end = False` locally. No Razorpay
   call, no Celery task interaction — the scheduled finalize task's own
   check-before-act logic (step 2 above) makes this safe even if it fires
   in a race with this request.
5. Audit-log `action="billing.subscription_reactivated"`.
6. Return the refreshed `SubscriptionStatusResponse`.

The service methods live in `BillingService`; the new Celery task lives in
`app/workers/tasks.py` (matching where every other task already lives) —
no new service file, no new task module.

### Frontend

`billing.html`: one new button, shown conditionally next to the existing
subscription-status block (not inside the plan grid — this acts on the
*current* subscription, not on choosing a new plan):
- Visible when `sub.status` is `active` or `past_due` (an owner mid-checkout
  or on a `trialing` plan that never activated has nothing to cancel yet in
  the way this endpoint means) AND `!sub.cancel_at_period_end`: **"Cancel subscription"**.
- Visible when `sub.cancel_at_period_end` is `true` AND the period hasn't
  ended: **"Keep my plan"** (the reactivate action), shown in the same slot
  as — and replacing — the cancel button, right next to the existing
  cancel-notice text ("Ends {date}").

`billing.js`:
- `confirm("Cancel your subscription? You'll keep access until {date}.")`
  before calling `POST /billing/cancel` — an accidentally-clicked cancel on
  a real paying tenant's account is a real-world-consequential mistake,
  matching this codebase's existing convention (`admin-monitors.js`'s
  force-logout confirm, `billing.js`'s own future task file's convention).
- No confirm needed for reactivate — it's the undo action, and Doc-3-style
  "forgiveness over confirmation" (per Apple's design principles reference
  already used elsewhere in this session's design work) suggests reversible
  actions don't need a confirmation gate the way destructive ones do.
- On success, re-render the subscription block from the response directly
  (no extra `GET /billing/subscription` round trip — the two new endpoints
  return the same `SubscriptionStatusResponse` shape `renderSubscription()`
  already consumes).
- Reuses the existing `qb-glass`/button styling already established on this
  page — no new CSS.

## Testing

- **Unit**: `tests/unit/test_billing_*.py` (extend or create, matching this
  codebase's per-service test-file convention) — one test per: successful
  cancel, already-canceled 409, no-subscription 404, successful reactivate,
  not-canceled 409, already-ended 409, and a Manager/Staff role rejection
  test for both new routes (mirroring `test_rbac.py`'s generic
  `require_role` coverage — no new test needed there specifically, per the
  established convention that generic guard behavior doesn't need
  per-endpoint re-proving).
- **Live verification**: this session already has a real Owner test account
  (`nrupal85@gmail.com`, tenant `51493719-2cee-4809-b5e6-00dbd1b7462a`,
  created through the real signup flow) from the prior SDD testing pass —
  reuse it rather than creating another. Razorpay isn't configured in this
  dev environment (confirmed earlier: `create_checkout_order` 409s with
  `PLAN_NOT_PROVISIONED`), so live end-to-end testing of the actual
  Razorpay API call is not possible here — cancel/reactivate should be
  unit-tested with the Razorpay client mocked (matching
  `create_checkout_order`'s own test convention), and the plan should note
  this environment limitation explicitly rather than attempt to fake it.

## Security notes

- Both new endpoints: `require_role(RoleLevel.OWNER)`, rate-limited,
  audit-logged — matching every mutating billing/admin action pattern
  already established across this codebase.
- No new PII, no new encrypted fields — this only touches the existing
  `payment.subscriptions` row's `cancel_at_period_end` boolean.

## File list

**Backend (modify):**
- `app/services/billing_service.py` — 2 new methods
- `app/api/v1/routers/billing.py` — 2 new routes
- `app/workers/tasks.py` — 1 new Celery task (`finalize_subscription_cancellation`)
- `app/schemas/billing.py` — likely no new schema needed (reuses
  `SubscriptionStatusResponse`); confirm during planning
- `tests/unit/test_billing_cancel.py` or extend existing billing test file
  (confirm exact existing file name/location during planning)

**Frontend (modify, in the main repo's WIP, not this worktree):**
- `app/templates/dashboard/billing.html` — cancel/reactivate button markup
- `static/js/billing.js` — button wiring, confirm(), response re-render

## Open questions for the implementation plan

- ~~Exact Razorpay API call for reactivation~~ — resolved above: no reversal
  API exists, so the design defers the real call to a period-end Celery
  task instead of assuming one.
- Exact current test file name for billing service unit tests (only
  `tests/unit/test_billing_webhook.py` was seen in this session's earlier
  exploration — confirm whether checkout/subscription-status already have
  their own test file, or need one created).
- How this codebase's existing Celery tasks call async code from a sync
  task function (`app/workers/tasks.py`'s established pattern — likely
  `asyncio.run()` wrapping an async DB/service call) — read an existing
  task before writing `finalize_subscription_cancellation` rather than
  guessing the pattern.
