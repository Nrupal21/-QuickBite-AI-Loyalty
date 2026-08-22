# Manager Billing-Checkout Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the one real Manager-visibility gap found while planning the Super Admin/Manager dashboard work — `POST /billing/checkout` currently has no server-side role restriction (any Manager or Staff can call it directly today), and `billing.js` is the only one of the three Owner-only dashboard surfaces that doesn't already hide its action from Manager/Staff.

**Architecture:** One-line backend fix (swap `get_current_user` for `require_role(RoleLevel.OWNER)` on the checkout route, exactly matching how `team.py` gates invite/deactivate) plus a small frontend change to `billing.js` mirroring the exact `CONNECT_ROLES`/`canConnect` pattern `google-profile.js` already uses for its own Owner-only buttons — no new abstractions.

**Tech Stack:** FastAPI + `require_role()` dependency (existing), vanilla JS (existing per-page script convention, no framework).

**Spec:** `docs/superpowers/specs/2026-08-22-super-admin-manager-dashboards-design.md` (Sub-project B, as revised after checking live code)

## Global Constraints

- Never use `session.query()` (sync) — this codebase is SQLAlchemy 2.x async only (AGENTS.md §4). N/A here (no new queries), noted for completeness.
- Error response shape is always `{"error": {"code": ..., "message": ...}}` (AGENTS.md §5) — the 403 this produces already has that shape, since it flows through `require_role`'s existing `HTTPException`.
- No PII in structlog output (AGENTS.md, CLAUDE.md) — N/A, no new logging in this plan.
- Follow existing per-page-script convention: role checks live inline in each page's own `static/js/*.js`, not in a shared helper (this was a deliberate choice during planning — see spec Sub-project B).

---

### Task 1: Require Owner role on the checkout endpoint

**Files:**
- Modify: `app/api/v1/routers/billing.py:64-78`
- Test: `tests/unit/test_rbac.py` (no new test needed — see step 3; this task's test is "the existing suite still passes")

**Interfaces:**
- Consumes: `require_role` from `app.api.v1.dependencies.auth` (already imported by `team.py`/`admin.py` with this exact signature: `require_role(min_level: int) -> Callable`), `RoleLevel` from `app.core.rbac` (already has `RoleLevel.OWNER = 2`).
- Produces: nothing new — this task only tightens an existing route's dependency.

- [ ] **Step 1: Read the current route to confirm the exact text to replace**

Run: view `app/api/v1/routers/billing.py` lines 1-30 (imports) and 64-78 (the route) to confirm nothing else in this file already imports `require_role`/`RoleLevel` under a different alias before editing.

- [ ] **Step 2: Add the import and swap the dependency**

In `app/api/v1/routers/billing.py`, the import block currently reads (line 17):

```python
from app.api.v1.dependencies.auth import get_current_user
```

Change it to:

```python
from app.api.v1.dependencies.auth import get_current_user, require_role
```

Add, alongside the other `from app.core...` imports near the top of the file:

```python
from app.core.rbac import RoleLevel
```

Then change the `create_checkout` route (currently):

```python
@router.post(
    "/checkout", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("10/hour")
async def create_checkout(
    request: Request,
    payload: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
```

to:

```python
@router.post(
    "/checkout", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("10/hour")
async def create_checkout(
    request: Request,
    payload: CheckoutRequest,
    # Doc 3: only an Owner (or Super Admin, which require_role admits at any
    # lower min_level per its "at least this senior" contract) may change a
    # tenant's subscription. Previously plain get_current_user — any Manager
    # or Staff account could call this directly, regardless of what the
    # dashboard UI chose to render.
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
```

Leave `GET /subscription` (line 56-61) on plain `get_current_user` — every role may *view* the current plan, only *changing* it is Owner-only.

- [ ] **Step 3: Run the full unit suite to confirm nothing broke and the generic guard covers this route**

Run: `pytest tests/unit/ -p no:langsmith_plugin -v --tb=short`
Expected: PASS, no failures. `test_rbac.py`'s `require_role` tests already prove the guard's Owner/Manager/Staff/Super-Admin behavior generically (`test_staff_on_manager_only_endpoint_returns_403` and neighbors) — this route now uses that same proven dependency, so no new test is needed to prove the 403 mechanics. (Note the `-p no:langsmith_plugin` flag — this repo's pytest run fails to collect without it, per this session's own memory of the project.)

- [ ] **Step 4: Commit**

```bash
git add app/api/v1/routers/billing.py
git commit -m "fix(billing): require Owner role to create a checkout order

POST /billing/checkout previously accepted any authenticated staff
account (Manager, Staff) — only the dashboard UI's absence of a
checkout button kept them from changing the tenant's plan. Gate it
server-side with require_role(OWNER), matching team.py's invite/
deactivate routes and Doc 3's billing permission matrix."
```

---

### Task 2: Hide the "Choose plan" button from Manager/Staff in the UI

**Files:**
- Modify: `static/js/billing.js:12-36` (top of file, session read) and `:146-160` (`planCardHtml`)
- Test: manual — this is a static per-page script with no existing JS unit test harness in this repo (none of `dashboard.js`/`google-profile.js`/`settings.js` have one either); verified by loading the page as each role (Task 3).

**Interfaces:**
- Consumes: `session.role` — a string already present on the `quickbite_staff_session` sessionStorage object every dashboard page reads (populated at login; `google-profile.js:70-74` and `settings.js` already read the identical field the identical way).
- Produces: nothing consumed by another task — this is the last file this plan touches.

- [ ] **Step 1: Add the role gate constant and computed flag**

In `static/js/billing.js`, immediately after the existing `session` read (currently ends at line ~36 with the `if (!session || !session.access_token)` redirect guard), add:

```javascript
  // Doc 3: only Owner (and Super Admin) may change the tenant's plan.
  // Mirrors CONNECT_ROLES in google-profile.js and MANAGE_TEAM_ROLES in
  // settings.js exactly — this codebase's established pattern is a small,
  // page-local role check per Owner-only action, not a shared helper.
  var CAN_CHECKOUT = { SUPER_ADMIN: true, OWNER: true };
  var canCheckout = !!(session.role && CAN_CHECKOUT[session.role]);
```

- [ ] **Step 2: Gate the button in `planCardHtml`**

Change:

```javascript
      (isCurrent
        ? ''
        : '<button type="button" class="qb-plan-cta" data-choose-plan data-plan-id="' + plan.id + '">Choose plan</button>') +
```

to:

```javascript
      (isCurrent || !canCheckout
        ? ''
        : '<button type="button" class="qb-plan-cta" data-choose-plan data-plan-id="' + plan.id + '">Choose plan</button>') +
```

A Manager/Staff viewing the page now sees every plan card (price, features, current-plan badge) but no "Choose plan" button on any of them — consistent with `google-profile.js` rendering no Connect/Disconnect button for the same roles.

- [ ] **Step 3: Commit**

```bash
git add static/js/billing.js
git commit -m "fix(billing): hide Choose plan button from Manager/Staff

Matches the existing CONNECT_ROLES pattern in google-profile.js and
MANAGE_TEAM_ROLES in settings.js. Plan cards still render fully
(price, features, current-plan badge) — only the mutating action is
gated, consistent with how the other two Owner-only surfaces already
behave."
```

---

### Task 3: Manual verification across roles

**Files:** none (verification only)

**Interfaces:**
- Consumes: the running dev server, three staff accounts (or JWTs minted for Super Admin / Owner / Manager) against a tenant with at least one non-current plan.

- [ ] **Step 1: Start the dev server**

Run: `docker-compose up -d` (per AGENTS.md's documented dev workflow), then run the FastAPI app per this repo's existing run configuration.

- [ ] **Step 2: Verify as Owner**

Log in as an Owner-role account, open `/dashboard/billing`. Confirm every non-current plan card shows a "Choose plan" button, and clicking one still successfully calls `POST /billing/checkout` (still 201/200, not 403 — confirming Task 1 didn't accidentally lock out Owner too).

- [ ] **Step 3: Verify as Manager**

Log in as a Manager-role account, open `/dashboard/billing`. Confirm plan cards render (price/features/current-plan badge all visible) but no "Choose plan" button appears on any card. Confirm directly calling `POST /api/v1/billing/checkout` with the Manager's bearer token (e.g. via curl/Postman) returns `403 INSUFFICIENT_PERMISSIONS` — this is the actual security fix from Task 1; the missing button is only the matching UI cleanup.

- [ ] **Step 4: Verify as Super Admin**

Confirm a Super Admin session also sees the "Choose plan" button (per `require_role`'s "at least this senior" semantics, Super Admin passes any `require_role(OWNER)` guard) — same as Owner in step 2.

No commit for this task — it's a verification checkpoint, not a code change. If any step fails, return to Task 1 or 2 to fix before considering this plan complete.
