# Super Admin Tenant Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current flat tenant table (`admin/index.html`, `admin-tenants.js`) with a fresh triage view — a search/filter/sort list where each row opens a slide-in detail panel carrying plan/subscription context, activity signals, and every existing admin action for that tenant.

**Architecture:** One backend task enriches `AdminService.list_tenants` with four grouped aggregate queries (subscription+plan join, branch counts, staff counts, last-audit-activity) run inside the existing `admin_bypass_context`, no migration. One frontend task replaces `admin/index.html`'s body and rewrites `admin-tenants.js` from scratch, adding one small new CSS block for the slide-in panel (nothing existing matches a full-height drawer). A verification task confirms the whole thing end-to-end against the real dev DB.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, Jinja2, Tailwind CDN + `qb-glass` CSS (existing, extended by one new panel component).

**Spec:** `docs/superpowers/specs/2026-08-25-admin-tenant-management-design.md`

## Global Constraints

- Every route in `app/api/v1/routers/admin.py` stays `require_role(RoleLevel.SUPER_ADMIN)`-gated — no change to that boundary, no new routes needed by this plan.
- `restaurant.tenants` is RLS-exempt (no bypass needed for the base tenant query); `payment.subscriptions`, `restaurant.branches`, `restaurant.users`, `restaurant.audit_logs` are all RLS-protected — every new aggregate query against them runs inside `rls.admin_bypass_context(self.session)`, matching every other cross-tenant read in this file.
- No new migration — every new field is derived from existing columns (`Tenant.created_at` via the `Base` mixin, `AuditLog.created_at` via `MAX()`, `COUNT()` on existing FK columns).
- `pytest` needs `-p no:langsmith_plugin` to collect in this repo.
- This worktree must branch from `sdd/super-admin-manager-dashboards` (already the base of this worktree — confirm before assuming, don't re-derive).

---

### Task 1: Backend — enriched `TenantSummary` + `list_tenants` aggregation

**Files:**
- Modify: `app/schemas/admin.py`
- Modify: `app/services/admin_service.py`
- Test: `tests/unit/test_admin_service.py`

**Interfaces:**
- Consumes: `Tenant`, `Subscription`, `SubscriptionPlan`, `Branch`, `AuditLog`, `User` models (all existing); `rls.admin_bypass_context` (existing).
- Produces: `TenantSummary` gains `plan_name: str | None`, `subscription_status: str`, `branch_count: int`, `staff_count: int`, `created_at: datetime`, `last_active_at: datetime | None`. `AdminService.list_tenants()` signature is unchanged (`() -> TenantListResponse`) — Task 2's frontend consumes these new fields from the same `GET /admin/tenants` response it already calls.

- [ ] **Step 1: Write the failing service tests**

Add to `tests/unit/test_admin_service.py`, right after the existing `test_list_tenants_returns_every_tenant_regardless_of_caller` test (in the `# --- list_tenants ---` section). Add these two imports to the file's existing import block first:

```python
from app.db.models.branch import Branch
from app.db.models.subscription import SubscriptionPlan
```

Then add the tests:

```python
@pytest.mark.asyncio
async def test_list_tenants_includes_plan_subscription_status_counts_and_activity():
    """Enriched fields: plan name + subscription status (joined), branch and
    staff counts (grouped COUNT), last-active (grouped MAX on audit_logs)."""
    tenant = make_tenant(name="Marco's")
    now = datetime.now(timezone.utc)

    session = make_session([
        [tenant],                              # 1. base tenant list
        [(tenant.id, "active", "Pro")],        # 2. subscription+plan join: (tenant_id, status, display_name)
        [(tenant.id, 3)],                      # 3. branch counts: (tenant_id, count)
        [(tenant.id, 5)],                      # 4. staff counts: (tenant_id, count)
        [(tenant.id, now)],                    # 5. last-active: (tenant_id, max(created_at))
    ])

    response = await AdminService(session=session).list_tenants()

    summary = response.tenants[0]
    assert summary.plan_name == "Pro"
    assert summary.subscription_status == "active"
    assert summary.branch_count == 3
    assert summary.staff_count == 5
    assert summary.last_active_at == now
    assert summary.created_at == tenant.created_at


@pytest.mark.asyncio
async def test_list_tenants_defaults_when_no_subscription_branches_or_activity():
    """A brand-new tenant: no Subscription row yet, no branches, no staff
    beyond the Owner who hasn't logged in again since signup, no audit
    history at all. Every enriched field must degrade gracefully, not error."""
    tenant = make_tenant(name="New Diner")

    session = make_session([
        [tenant],   # 1. base tenant list
        [],         # 2. no subscription row for this tenant
        [],         # 3. no branches
        [],         # 4. no staff counted (edge case: even the Owner not yet counted)
        [],         # 5. no audit history
    ])

    response = await AdminService(session=session).list_tenants()

    summary = response.tenants[0]
    assert summary.plan_name is None
    assert summary.subscription_status == "none"
    assert summary.branch_count == 0
    assert summary.staff_count == 0
    assert summary.last_active_at is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_admin_service.py -k "test_list_tenants_includes or test_list_tenants_defaults" -p no:langsmith_plugin -v`
Expected: FAIL — `AttributeError: 'TenantSummary' object has no attribute 'plan_name'` (the schema doesn't have these fields yet).

- [ ] **Step 3: Extend `TenantSummary` in `app/schemas/admin.py`**

Replace the existing `TenantSummary` class:

```python
class TenantSummary(BaseModel):
    tenant_id: uuid.UUID
    subdomain: str
    name: str
    onboarding_state: str
    is_active: bool
    plan_name: str | None
    subscription_status: str  # "none" | "trialing" | "active" | "past_due" | "canceled" | "paused"
    branch_count: int
    staff_count: int
    created_at: datetime
    last_active_at: datetime | None
```

- [ ] **Step 4: Enrich `list_tenants` in `app/services/admin_service.py`**

Add `SubscriptionPlan` to the existing `from app.db.models.subscription import Subscription` import (becomes `from app.db.models.subscription import Subscription, SubscriptionPlan`). Add a new import for `Branch`:

```python
from app.db.models.branch import Branch
```

Replace the existing `list_tenants` method:

```python
    async def list_tenants(self) -> TenantListResponse:
        result = await self.session.execute(select(Tenant).order_by(Tenant.name))
        tenants = result.scalars().all()

        # Four grouped aggregates, one pass each — not N+1 per tenant.
        # All four source tables are RLS-protected (restaurant.branches,
        # restaurant.users, restaurant.audit_logs, payment.subscriptions),
        # so this whole block runs inside the same bypass every other
        # cross-tenant read in this file uses.
        async with rls.admin_bypass_context(self.session):
            sub_result = await self.session.execute(
                select(
                    Subscription.tenant_id, Subscription.status, SubscriptionPlan.display_name
                ).join(SubscriptionPlan, Subscription.plan_id == SubscriptionPlan.id)
            )
            sub_by_tenant: dict[uuid.UUID, tuple[str, str]] = {
                row[0]: (row[1], row[2]) for row in sub_result.all()
            }

            branch_result = await self.session.execute(
                select(Branch.tenant_id, func.count()).group_by(Branch.tenant_id)
            )
            branch_counts: dict[uuid.UUID, int] = {row[0]: row[1] for row in branch_result.all()}

            staff_result = await self.session.execute(
                select(User.tenant_id, func.count())
                .where(User.tenant_id.is_not(None))
                .group_by(User.tenant_id)
            )
            staff_counts: dict[uuid.UUID, int] = {row[0]: row[1] for row in staff_result.all()}

            activity_result = await self.session.execute(
                select(AuditLog.tenant_id, func.max(AuditLog.created_at))
                .where(AuditLog.tenant_id.is_not(None))
                .group_by(AuditLog.tenant_id)
            )
            last_active: dict[uuid.UUID, datetime] = {
                row[0]: row[1] for row in activity_result.all()
            }

        return TenantListResponse(
            tenants=[
                TenantSummary(
                    tenant_id=tenant.id,
                    subdomain=tenant.subdomain,
                    name=tenant.name,
                    onboarding_state=tenant.onboarding_state,
                    is_active=tenant.is_active,
                    plan_name=sub_by_tenant[tenant.id][1] if tenant.id in sub_by_tenant else None,
                    subscription_status=sub_by_tenant.get(tenant.id, ("none", None))[0],
                    branch_count=branch_counts.get(tenant.id, 0),
                    staff_count=staff_counts.get(tenant.id, 0),
                    created_at=tenant.created_at,
                    last_active_at=last_active.get(tenant.id),
                )
                for tenant in tenants
            ]
        )
```

`datetime` is already imported at the top of `admin_service.py` (`from datetime import datetime, timedelta, timezone`) — do not add a duplicate import.

- [ ] **Step 5: Run to verify the new tests pass**

Run: `pytest tests/unit/test_admin_service.py -p no:langsmith_plugin -v`
Expected: PASS, every test in the file (including the pre-existing `test_list_tenants_returns_every_tenant_regardless_of_caller`, which must still pass unchanged — it only asserts on `.name`, not the new fields, so it stays green with no edit needed).

- [ ] **Step 6: Run the full suite and commit**

Run: `pytest tests/unit/ -p no:langsmith_plugin -q`
Expected: PASS, no regressions.

```bash
git add app/schemas/admin.py app/services/admin_service.py tests/unit/test_admin_service.py
git commit -m "feat(admin): enrich list_tenants with plan, subscription status, counts, and activity

Four grouped aggregate queries (subscription+plan join, branch count,
staff count, last-audit-activity) inside the existing admin_bypass_context.
No migration — every field derives from existing columns."
```

---

### Task 2: Frontend — fresh tenant list + slide-in detail panel

**Files:**
- Modify: `static/css/dashboard.css` — add the new panel component (append, don't restructure existing rules)
- Modify: `app/templates/admin/index.html` — body rewritten, head/shell unchanged
- Modify: `static/js/admin-tenants.js` — full rewrite

**Interfaces:**
- Consumes: `GET /admin/tenants` returning the Task-1-enriched `TenantSummary` fields; `GET /admin/health-metrics` (unchanged, existing KPI strip); `PATCH /admin/tenants/{id}/status`, `PATCH /admin/tenants/{id}/subscription`, `POST /admin/tenants/{id}/sync-gmb` (all existing, unchanged); `GET /admin/sessions?tenant_id=` returning `SessionSummary` rows (existing, unchanged); `POST /admin/users/{id}/force-logout` (existing, unchanged). `window.QuickBiteAdmin.apiFetch` (existing helper from `admin-shell.js`, unchanged).
- Produces: nothing consumed by another task — this is the last implementation task in this plan.

- [ ] **Step 1: Add the slide-in panel CSS to `static/css/dashboard.css`**

Nothing existing matches a full-height right-side drawer (`.qb-account-sheet` is a small 260px popover menu, not a detail panel — confirmed by reading its own rules, do not reuse it directly). Append this new block at the end of the file:

```css
/* -------------------------------------------------- admin detail panel */
/* Super Admin tenant-management slide-in — a genuine right-side drawer,
   distinct from .qb-account-sheet's small popover-menu sizing. Composes
   with .qb-glass in markup for the surface treatment, same pattern as
   every other qb-glass surface in this file. */

.qb-admin-panel-scrim {
  position: fixed;
  inset: 0;
  z-index: 210;
  background: rgba(11, 11, 13, 0.32);
  -webkit-backdrop-filter: blur(2px);
  backdrop-filter: blur(2px);
}

.qb-admin-panel {
  position: fixed;
  top: 0;
  right: 0;
  height: 100vh;
  width: 420px;
  max-width: 100vw;
  z-index: 220;
  overflow-y: auto;
  padding: 1.5rem;
  border-radius: 0;
}

@media (max-width: 640px) {
  .qb-admin-panel {
    width: 100vw;
  }
}

.qb-admin-panel-close {
  position: absolute;
  top: 1rem;
  right: 1rem;
  width: 2rem;
  height: 2rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  color: var(--muted);
  cursor: pointer;
}

.qb-admin-panel-close:hover {
  background: rgba(30, 42, 58, 0.06);
}

.qb-admin-panel-section {
  padding-top: 1.25rem;
  margin-top: 1.25rem;
  border-top: 1px solid var(--glass-border);
}

.qb-admin-panel-section:first-of-type {
  padding-top: 0;
  margin-top: 0;
  border-top: none;
}

.qb-admin-staff-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.625rem 0;
  border-bottom: 1px solid var(--glass-border);
}

.qb-admin-staff-row:last-child {
  border-bottom: none;
}
```

`--glass-border` and `--muted` are existing custom properties already defined at the top of this file (used throughout, e.g. `.qb-more-divider`, `.qb-more-nav-item .material-symbols-outlined`) — confirm they're in scope before writing, don't redefine them.

- [ ] **Step 2: Run to verify it doesn't break anything**

Run: `python -c "print('css has no test — verified by visual check in Task 3')"`
This step exists only so the plan's own step numbering stays consistent with the TDD shape used elsewhere; CSS has no automated test in this codebase's convention (confirmed: no `.css` files appear in any `tests/` path).

- [ ] **Step 3: Rewrite `app/templates/admin/index.html`'s body**

Keep the file's `<head>` (lines 1-66 of the current file: DOCTYPE, meta, title, fonts, Tailwind CDN + config, `dashboard.css` link) and the shared shell (`<body>` opening through the closing `</nav>` of the mobile tab bar, the account scrim/sheet block, and the `<main>` header) exactly as they are today — every other admin page uses this identical shell, don't diverge from it. Only the content inside `<main>`'s inner `<div class="p-5 ...">` changes, plus the two `<script>` tags stay the same (same filenames, new content per Step 4).

Replace everything from `<div class="p-5 min-[900px]:p-8 ...">` (the content wrapper) through the closing `</main>` with:

```html
  <div class="p-5 min-[900px]:p-8 pb-28 min-[900px]:pb-8 max-w-7xl mx-auto space-y-6">
    <div id="admin-error" class="hidden bg-brand-danger/10 text-brand-danger text-sm font-medium px-4 py-3 rounded-lg" role="alert"></div>

    <div class="qb-glass qb-kpi-strip qb-kpi-strip--3" style="grid-template-columns: repeat(3, 1fr);">
      <div class="qb-kpi">
        <div class="qb-kpi-icon qb-kpi-icon--blue" aria-hidden="true">
          <span class="material-symbols-outlined">apartment</span>
        </div>
        <div class="qb-kpi-body">
          <p class="qb-kpi-label">Active Tenants</p>
          <h3 class="qb-kpi-value" data-metric="active_tenants"><span class="qb-skel"></span></h3>
        </div>
      </div>
      <div class="qb-kpi">
        <div class="qb-kpi-icon qb-kpi-icon--warning" aria-hidden="true">
          <span class="material-symbols-outlined">block</span>
        </div>
        <div class="qb-kpi-body">
          <p class="qb-kpi-label">Suspended</p>
          <h3 class="qb-kpi-value" data-metric="suspended_tenants"><span class="qb-skel"></span></h3>
        </div>
      </div>
      <div class="qb-kpi">
        <div class="qb-kpi-icon qb-kpi-icon--orange" aria-hidden="true">
          <span class="material-symbols-outlined">warning</span>
        </div>
        <div class="qb-kpi-body">
          <p class="qb-kpi-label">Past Due</p>
          <h3 class="qb-kpi-value" data-metric="past_due_subscriptions"><span class="qb-skel"></span></h3>
        </div>
      </div>
    </div>

    <div class="qb-glass p-4 flex flex-wrap gap-3 items-center">
      <input type="search" class="qb-invite-input flex-1 min-w-[200px]" placeholder="Search by name or subdomain…" data-tenant-search />
      <select class="qb-invite-select" data-tenant-status-filter>
        <option value="">All statuses</option>
        <option value="active">Active</option>
        <option value="suspended">Suspended</option>
      </select>
      <select class="qb-invite-select" data-tenant-plan-filter>
        <option value="">All plans</option>
        <option value="none">No plan</option>
      </select>
      <select class="qb-invite-select" data-tenant-sort>
        <option value="signup_desc">Newest signup</option>
        <option value="signup_asc">Oldest signup</option>
        <option value="active_desc">Most recently active</option>
        <option value="active_asc">Least recently active</option>
      </select>
    </div>

    <div class="qb-glass overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs font-bold uppercase tracking-wide text-brand-muted border-b border-outline-variant/20">
            <th class="px-5 py-3">Tenant</th>
            <th class="px-5 py-3">Plan</th>
            <th class="px-5 py-3">Branches</th>
            <th class="px-5 py-3">Staff</th>
            <th class="px-5 py-3">Signed up</th>
            <th class="px-5 py-3">Last active</th>
          </tr>
        </thead>
        <tbody data-tenant-rows></tbody>
      </table>
      <div class="hidden p-10 text-center" data-tenant-empty>
        <span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">search_off</span>
        <p class="text-sm font-semibold text-brand-ink mt-2">No tenants match this filter</p>
      </div>
    </div>

    <template id="tenant-row-template">
      <tr class="border-b border-outline-variant/10 cursor-pointer hover:bg-white/40" data-tenant-row>
        <td class="px-5 py-3">
          <p class="font-semibold text-brand-ink" data-cell="name"></p>
          <p class="text-xs text-brand-muted" data-cell="subdomain"></p>
        </td>
        <td class="px-5 py-3">
          <span class="text-xs font-bold px-2.5 py-1 rounded-full" data-cell="plan-badge"></span>
        </td>
        <td class="px-5 py-3 text-brand-muted" data-cell="branch-count"></td>
        <td class="px-5 py-3 text-brand-muted" data-cell="staff-count"></td>
        <td class="px-5 py-3 text-brand-muted" data-cell="signup-date"></td>
        <td class="px-5 py-3 text-brand-muted" data-cell="last-active"></td>
      </tr>
    </template>

    <div class="qb-admin-panel-scrim hidden" data-panel-scrim></div>
    <div class="qb-glass qb-admin-panel hidden" data-tenant-panel role="dialog" aria-label="Tenant detail">
      <button type="button" class="qb-admin-panel-close" data-panel-close aria-label="Close">
        <span class="material-symbols-outlined" aria-hidden="true">close</span>
      </button>

      <div class="qb-admin-panel-section">
        <h3 class="text-lg font-bold text-brand-ink" data-panel-name></h3>
        <p class="text-sm text-brand-muted" data-panel-subdomain></p>
        <p class="text-xs text-brand-muted mt-1" data-panel-signup></p>
      </div>

      <div class="qb-admin-panel-section">
        <p class="text-xs font-bold uppercase tracking-wide text-brand-muted mb-2">Subscription</p>
        <p class="text-sm mb-3" data-panel-subscription></p>
        <form class="flex flex-wrap gap-3 items-end" data-override-form>
          <div class="qb-invite-field">
            <label class="qb-invite-label">Plan</label>
            <select class="qb-invite-select" name="plan_id" data-panel-plan-select>
              <option value="">— no change —</option>
            </select>
          </div>
          <div class="qb-invite-field">
            <label class="qb-invite-label">Status</label>
            <select class="qb-invite-select" name="status">
              <option value="">— no change —</option>
              <option value="trialing">Trialing</option>
              <option value="active">Active</option>
              <option value="past_due">Past due</option>
              <option value="canceled">Canceled</option>
              <option value="paused">Paused</option>
            </select>
          </div>
          <div class="qb-invite-field flex-1 min-w-[200px]">
            <label class="qb-invite-label">Reason (required)</label>
            <input class="qb-invite-input w-full" name="reason" required minlength="3" maxlength="500" />
          </div>
          <button type="submit" class="qb-invite-submit">Save override</button>
        </form>
      </div>

      <div class="qb-admin-panel-section">
        <p class="text-xs font-bold uppercase tracking-wide text-brand-muted mb-2">Actions</p>
        <div class="flex flex-wrap gap-2">
          <button type="button" class="qb-program-action" data-panel-action="toggle-status"></button>
          <button type="button" class="qb-program-action" data-panel-action="sync-gmb">Sync Google Profile</button>
        </div>
      </div>

      <div class="qb-admin-panel-section">
        <p class="text-xs font-bold uppercase tracking-wide text-brand-muted mb-2">Active staff sessions</p>
        <div data-panel-staff-rows></div>
        <p class="hidden text-sm text-brand-muted" data-panel-staff-empty>No active staff sessions.</p>
      </div>
    </div>
  </div>
</main>
```

- [ ] **Step 4: Confirm Jinja2 syntax still parses**

Run: `python -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('app/templates')); env.parse(env.loader.get_source(env, 'admin/index.html')[0]); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Rewrite `static/js/admin-tenants.js` from scratch**

Replace the entire file content:

```javascript
/* QuickBite AI + Loyalty — Super Admin tenant management: health KPI strip,
 * search/filter/sort tenant list, and a slide-in detail panel per tenant
 * (subscription override, suspend/activate, sync GMB, staff sessions with
 * per-user force-logout). Replaces the old flat-table page wholesale.
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var admin = window.QuickBiteAdmin;
    if (!admin) return;

    function showError(message) {
      var box = document.getElementById('admin-error');
      if (!box) return;
      box.textContent = message;
      box.classList.remove('hidden');
    }

    function clearError() {
      var box = document.getElementById('admin-error');
      if (!box) return;
      box.textContent = '';
      box.classList.add('hidden');
    }

    function formatDate(iso) {
      if (!iso) return '—';
      return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    }

    function formatRelative(iso) {
      if (!iso) return 'Never';
      var diffMs = Date.now() - new Date(iso).getTime();
      var days = Math.floor(diffMs / 86400000);
      if (days <= 0) return 'Today';
      if (days === 1) return 'Yesterday';
      if (days < 30) return days + ' days ago';
      var months = Math.floor(days / 30);
      if (months < 12) return months + (months === 1 ? ' month ago' : ' months ago');
      var years = Math.floor(months / 12);
      return years + (years === 1 ? ' year ago' : ' years ago');
    }

    // ---------- health KPI strip (unchanged from the old page) ----------

    function loadMetrics() {
      admin.apiFetch('/admin/health-metrics').then(function (data) {
        Object.keys(data).forEach(function (key) {
          var el = document.querySelector('[data-metric="' + key + '"]');
          if (el) el.textContent = data[key];
        });
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    // ---------- tenant list: search / filter / sort ----------

    var rowsMount = document.querySelector('[data-tenant-rows]');
    var emptyState = document.querySelector('[data-tenant-empty]');
    var rowTemplate = document.getElementById('tenant-row-template');
    var allTenants = [];

    var SUBSCRIPTION_BADGE = {
      none: 'bg-brand-muted/10 text-brand-muted',
      trialing: 'bg-brand-accent-orange/10 text-brand-accent-orange',
      active: 'bg-brand-success/10 text-brand-success',
      past_due: 'bg-brand-warning/10 text-brand-warning',
      canceled: 'bg-brand-danger/10 text-brand-danger',
      paused: 'bg-brand-muted/10 text-brand-muted',
    };

    function planBadgeText(tenant) {
      if (!tenant.plan_name) return 'No plan';
      return tenant.plan_name + ' · ' + tenant.subscription_status.replace(/_/g, ' ');
    }

    function renderRow(tenant) {
      var frag = rowTemplate.content.cloneNode(true);
      var row = frag.querySelector('[data-tenant-row]');

      row.querySelector('[data-cell="name"]').textContent = tenant.name;
      row.querySelector('[data-cell="subdomain"]').textContent = tenant.subdomain;

      var badge = row.querySelector('[data-cell="plan-badge"]');
      badge.textContent = planBadgeText(tenant);
      badge.className = 'text-xs font-bold px-2.5 py-1 rounded-full ' +
        (SUBSCRIPTION_BADGE[tenant.subscription_status] || SUBSCRIPTION_BADGE.none);

      row.querySelector('[data-cell="branch-count"]').textContent = tenant.branch_count;
      row.querySelector('[data-cell="staff-count"]').textContent = tenant.staff_count;
      row.querySelector('[data-cell="signup-date"]').textContent = formatDate(tenant.created_at);
      row.querySelector('[data-cell="last-active"]').textContent = formatRelative(tenant.last_active_at);

      row.addEventListener('click', function () {
        openPanel(tenant);
      });

      return frag;
    }

    function renderTenants(list) {
      rowsMount.innerHTML = '';
      list.forEach(function (tenant) {
        rowsMount.appendChild(renderRow(tenant));
      });
      emptyState.classList.toggle('hidden', list.length > 0);
    }

    var searchInput = document.querySelector('[data-tenant-search]');
    var statusFilter = document.querySelector('[data-tenant-status-filter]');
    var planFilter = document.querySelector('[data-tenant-plan-filter]');
    var sortSelect = document.querySelector('[data-tenant-sort]');

    function applyFiltersAndSort() {
      var term = searchInput.value.trim().toLowerCase();
      var status = statusFilter.value;
      var plan = planFilter.value;
      var sort = sortSelect.value;

      var filtered = allTenants.filter(function (t) {
        if (term && t.name.toLowerCase().indexOf(term) === -1 && t.subdomain.toLowerCase().indexOf(term) === -1) return false;
        if (status === 'active' && !t.is_active) return false;
        if (status === 'suspended' && t.is_active) return false;
        if (plan === 'none' && t.plan_name) return false;
        if (plan && plan !== 'none' && t.plan_name !== plan) return false;
        return true;
      });

      filtered.sort(function (a, b) {
        if (sort === 'signup_asc') return new Date(a.created_at) - new Date(b.created_at);
        if (sort === 'active_desc') return new Date(b.last_active_at || 0) - new Date(a.last_active_at || 0);
        if (sort === 'active_asc') return new Date(a.last_active_at || 0) - new Date(b.last_active_at || 0);
        return new Date(b.created_at) - new Date(a.created_at); // signup_desc, default
      });

      renderTenants(filtered);
    }

    [searchInput, statusFilter, planFilter, sortSelect].forEach(function (el) {
      if (el) el.addEventListener(searchInput === el ? 'input' : 'change', applyFiltersAndSort);
    });

    function populatePlanFilterOptions() {
      var planNames = Array.from(new Set(allTenants.map(function (t) { return t.plan_name; }).filter(Boolean)));
      planNames.forEach(function (name) {
        var opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        planFilter.appendChild(opt);
      });
    }

    function loadTenants() {
      return admin.apiFetch('/admin/tenants').then(function (data) {
        allTenants = data.tenants;
        populatePlanFilterOptions();
        applyFiltersAndSort();
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    // ---------- detail panel ----------

    var panel = document.querySelector('[data-tenant-panel]');
    var panelScrim = document.querySelector('[data-panel-scrim]');
    var currentTenant = null;

    function closePanel() {
      panel.classList.add('hidden');
      panelScrim.classList.add('hidden');
      currentTenant = null;
    }

    function openPanel(tenant) {
      currentTenant = tenant;
      panel.querySelector('[data-panel-name]').textContent = tenant.name;
      panel.querySelector('[data-panel-subdomain]').textContent = tenant.subdomain;
      panel.querySelector('[data-panel-signup]').textContent = 'Signed up ' + formatDate(tenant.created_at);
      panel.querySelector('[data-panel-subscription]').textContent = planBadgeText(tenant);

      var statusBtn = panel.querySelector('[data-panel-action="toggle-status"]');
      statusBtn.textContent = tenant.is_active ? 'Suspend tenant' : 'Reactivate tenant';

      loadStaffSessions(tenant.tenant_id);
      panel.classList.remove('hidden');
      panelScrim.classList.remove('hidden');
    }

    panel.querySelector('[data-panel-close]').addEventListener('click', closePanel);
    panelScrim.addEventListener('click', closePanel);

    panel.querySelector('[data-panel-action="toggle-status"]').addEventListener('click', function () {
      if (!currentTenant) return;
      var nextState = !currentTenant.is_active;
      var verb = nextState ? 'reactivate' : 'suspend';
      if (!window.confirm('Are you sure you want to ' + verb + ' ' + currentTenant.name + '?')) return;

      clearError();
      admin.apiFetch('/admin/tenants/' + currentTenant.tenant_id + '/status', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: nextState }),
      }).then(function () {
        closePanel();
        loadTenants();
        loadMetrics();
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    });

    panel.querySelector('[data-panel-action="sync-gmb"]').addEventListener('click', function () {
      if (!currentTenant) return;
      clearError();
      admin.apiFetch('/admin/tenants/' + currentTenant.tenant_id + '/sync-gmb', { method: 'POST' })
        .catch(function (error) {
          if (error.message !== 'unauthorized') showError(error.message);
        });
    });

    var overrideForm = panel.querySelector('[data-override-form]');
    overrideForm.addEventListener('submit', function (event) {
      event.preventDefault();
      if (!currentTenant) return;
      var formData = new FormData(overrideForm);
      var body = { reason: formData.get('reason') };
      var status = formData.get('status');
      var planId = formData.get('plan_id');
      if (status) body.status = status;
      if (planId) body.plan_id = planId;

      clearError();
      admin.apiFetch('/admin/tenants/' + currentTenant.tenant_id + '/subscription', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(function () {
        overrideForm.reset();
        loadTenants();
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    });

    // ---------- staff sessions (per-tenant) ----------

    var staffRowsMount = panel.querySelector('[data-panel-staff-rows]');
    var staffEmpty = panel.querySelector('[data-panel-staff-empty]');

    function loadStaffSessions(tenantId) {
      staffRowsMount.innerHTML = '';
      staffEmpty.classList.add('hidden');
      admin.apiFetch('/admin/sessions?tenant_id=' + tenantId).then(function (data) {
        var active = data.sessions.filter(function (s) { return !s.revoked; });
        if (!active.length) {
          staffEmpty.classList.remove('hidden');
          return;
        }
        active.forEach(function (session) {
          var row = document.createElement('div');
          row.className = 'qb-admin-staff-row';
          var info = document.createElement('span');
          info.className = 'text-sm text-brand-muted';
          info.textContent = 'User ' + session.user_id.slice(0, 8) + '… · expires ' + formatDate(session.expires_at);
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'qb-program-action';
          btn.textContent = 'Force logout';
          btn.addEventListener('click', function () {
            if (!window.confirm('Force logout this user?')) return;
            clearError();
            admin.apiFetch('/admin/users/' + session.user_id + '/force-logout', { method: 'POST' })
              .then(function () {
                loadStaffSessions(tenantId);
              })
              .catch(function (error) {
                if (error.message !== 'unauthorized') showError(error.message);
              });
          });
          row.appendChild(info);
          row.appendChild(btn);
          staffRowsMount.appendChild(row);
        });
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    // ---------- plan options for the override form ----------

    function loadPlanOptions() {
      admin.apiFetch('/billing/plans').then(function (data) {
        var select = panel.querySelector('[data-panel-plan-select]');
        data.forEach(function (plan) {
          var opt = document.createElement('option');
          opt.value = plan.id;
          opt.textContent = plan.display_name;
          select.appendChild(opt);
        });
      }).catch(function () {
        // Non-fatal: the override form still works without plan_id (status-only change).
      });
    }

    loadMetrics();
    loadTenants();
    loadPlanOptions();
  });
})();
```

`GET /billing/plans` is an existing, already-public endpoint (`app/api/v1/routers/billing.py`, no auth required — confirmed in the billing sub-project's own earlier work this session) — reused here to populate the override form's plan dropdown rather than adding a new admin-scoped plans endpoint.

- [ ] **Step 6: Confirm JS syntax is valid**

Run: `node --check static/js/admin-tenants.js`
Expected: no output (valid syntax).

- [ ] **Step 7: Commit**

```bash
git add static/css/dashboard.css app/templates/admin/index.html static/js/admin-tenants.js
git commit -m "feat(admin): rewrite tenant management as a search/filter/sort list with a slide-in detail panel

Replaces the old flat table wholesale — new admin-tenants.js, new
admin/index.html body, one new CSS component (.qb-admin-panel) for
the detail drawer. Every existing action (suspend/activate, plan
override, sync GMB, per-user force-logout) is preserved, now scoped
to the panel instead of an inline table row."
```

---

### Task 3: Manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `pytest tests/unit/ -p no:langsmith_plugin -q`
Expected: PASS, no regressions.

Run: `ruff check app/ tests/`
Expected: All checks passed.

- [ ] **Step 2: Start the dev server and verify with a Super Admin session**

Start the app in this worktree (`uvicorn app.main:app --port <free-port>`, matching this session's established pattern for isolated-worktree verification). No self-service Super Admin signup exists in this product. `app/db/models/user.py`'s own docstring states every role except `USER` "always carries a concrete tenant_id" — do NOT assume `tenant_id=None` for a Super Admin JWT. Instead, query the dev DB directly for an existing `User` row with a Super Admin role (join `restaurant.users` to `static.roles` where `roles.name = 'SUPER_ADMIN'`) and use that row's real `id` and `tenant_id` with `app.core.security.create_access_token(user_id, tenant_id, role="SUPER_ADMIN")`. If no such row exists yet in the dev DB, this step surfaces that as a blocker to report, not something to work around by inventing test data.

Confirm:
- `GET /admin` renders 200, the KPI strip populates.
- The tenant list shows plan badge, branch count, staff count, signup date, and a relative last-active label (or "Never") for every tenant.
- Search narrows the list by name/subdomain; status and plan filters narrow correctly; sort reorders by signup/last-active in both directions.
- Clicking a row opens the slide-in panel with that tenant's name, subdomain, signup date, and subscription summary.
- The subscription-override form submits successfully and the list re-reflects the change (a real disclosed test if this requires writing to the dev DB — do not seed data without asking first, per this session's standing rule).
- The suspend/reactivate action toggles `is_active` and the panel closes/list refreshes.
- The staff-sessions section lists active sessions for that tenant (or shows the empty state) and force-logout works.

No commit for this task — verification only. If any step fails, return to the relevant task to fix before considering this plan complete.
