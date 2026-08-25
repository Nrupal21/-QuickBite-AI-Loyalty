# Super Admin Tenant Management — Fresh Rebuild — Design Spec

Date: 2026-08-25
Status: approved for planning

## Problem

This is sub-project 1 of 4 in a larger "fully fresh Super Admin pages" request
(the other three — User/API Monitors + Audit Logs rebuild, System Health/Ops,
Platform Revenue & Billing Overview — are separate, later specs).

The current `app/templates/admin/index.html` (built earlier this session on
`sdd/super-admin-manager-dashboards`) lists tenants with only subdomain, name,
onboarding state, and active/suspended — a name list, not a triage view. The
user explicitly rejected reusing or patching this file: both its feature set
and its design need to be rebuilt from scratch. `app/templates/admin/index.html`
will not be read for reuse during implementation — a genuinely new file
replaces it.

## Out of scope

- The other 3 sub-projects (separate specs, separate SDD cycles).
- `admin/audit_logs.html` and `admin/monitors.html` — untouched by this
  sub-project; they get their own rebuild next.
- Pagination — tenant counts are in the tens/hundreds for this product today,
  not a scale problem yet; all filtering happens client-side over one
  `GET /admin/tenants` payload, matching the existing endpoint's shape.
- A tenant detail *page* — the user chose a slide-in side panel over a
  separate `/admin/tenants/{id}` route.
- New tenant actions beyond what already exists (suspend/activate,
  subscription override, sync-GMB, force-logout). No delete/archive, no
  impersonation — not requested.

## Design

### Backend — `AdminService.list_tenants` enrichment

**Resolved during planning:** every new field this needs is derivable from
existing tables with no migration:
- Plan name & subscription status: join `Subscription` (by `tenant_id`) →
  `SubscriptionPlan` (by `plan_id`), same join `BillingService.get_subscription_status`
  already does for the Owner-facing view. A tenant with no `Subscription` row
  (real state confirmed this session — the dev tenant has none) returns
  `plan_name: null`, `subscription_status: "none"`.
- Branch count: `COUNT(*)` from `restaurant.branches` grouped by `tenant_id`.
- Staff count: `COUNT(*)` from `restaurant.users` where `tenant_id = X AND
  role != CUSTOMER` (mirrors the role exclusion already used elsewhere in
  this codebase for "staff" vs. "diner" counts).
- Signup date: `Tenant.created_at` — already exists via the `Base` mixin, the
  current schema just never surfaced it.
- Last active: `MAX(AuditLog.created_at)` for that tenant. No new column —
  `AuditLog` already gets a row on every `login_success`/`login_failed`/
  `mfa_enrollment_required` event (confirmed this session, in the RLS-context
  bugfix on `complete_authentication`). `NULL` when a tenant has no audit
  history yet (e.g. registered but never logged in).

All of the above run as one aggregation pass (or a small number of grouped
queries) inside `AdminService.list_tenants` — this already runs under
`admin_bypass_context` (cross-tenant BYPASSRLS), so no RLS changes needed.
Query cost is bounded by tenant count, which is small; no pagination needed
per the Out-of-scope note above.

`TenantSummary` (schema) grows these fields:
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

**Per-tenant staff list for the side panel** (new endpoint, small): the panel
needs a per-tenant staff roster to show alongside the existing per-user
force-logout action. `GET /admin/sessions?tenant_id=X` already exists and
returns `SessionSummary` rows (one per active session, with `user_id`) — this
is enough to drive "which staff members have an active session, force-logout
each" without a new endpoint. A tenant with zero active sessions shows an
empty state in the panel, not an error.

No other backend routes change. `TenantStatusUpdateRequest`/`Response`,
`SubscriptionOverrideRequest`/`Response`, `GmbSyncResponse` are reused as-is.

### Frontend — `app/templates/admin/index.html` (rewritten from scratch)

**List page:**
- Search box (name/subdomain, client-side substring filter).
- Filter chips: status (All / Active / Suspended), plan (All / Starter / Pro
  / Enterprise / No plan).
- Sort control: signup date, last-active date (both directions).
- Each row: name, subdomain, plan badge, subscription-status badge
  (color-coded per the existing `qb-sub-status--{status}` convention already
  shipped for the Owner billing page — reused, not reinvented), branch count,
  staff count, last-active (relative, e.g. "3 days ago"; "Never" when null).
- Row click opens the side panel — reuses the `qb-account-sheet` slide-in
  pattern already shipped for the mobile more-menu (new to desktop scale
  here, but a real precedent, not an invented pattern).

**Side panel contents:**
- Tenant identity header (name, subdomain, signup date).
- Subscription section: current plan/status, the existing override form
  (`PATCH /admin/tenants/{id}/subscription` — plan_id, status,
  trial_ends_at, reason).
- Status toggle: suspend/activate (`PATCH /admin/tenants/{id}/status`).
- Sync GMB button (`POST /admin/tenants/{id}/sync-gmb`).
- Staff roster: one row per active session from `GET /admin/sessions?tenant_id=X`
  (user_id, ip_address_hash truncated for display, expires_at), each with a
  "Force logout" button (`POST /admin/users/{user_id}/force-logout`).

**Design system:** `qb-glass` throughout — Doc 4 palette, Hanken Grotesk,
`static/css/dashboard.css` classes — but a genuinely new layout (list +
side-panel triage view, not the old page's flat table), matching the user's
"keep qb-glass, redesign within it" decision.

## Testing

- **Unit**: `tests/unit/test_admin_service.py` — one test per new aggregation
  in `list_tenants` (plan join present/absent, branch/staff counts, last-active
  present/null), matching this file's existing `make_session`/`added()`
  mocking conventions.
- **Live verification**: reuse the real test tenant/accounts from this
  session's earlier real-signup testing (Owner `nrupal85@gmail.com`, Manager
  `horizonf0221@gmail.com`, tenant `51493719-2cee-4809-b5e6-00dbd1b7462a`) —
  no Super Admin test account exists yet in the dev DB; minting a Super Admin
  JWT directly (same `create_access_token` fallback used for the billing
  sub-project's live verification) is the practical path, since Super Admin
  accounts aren't self-service signups in this product.

## Security notes

- `list_tenants`, the new staff-roster lookup, and every existing action all
  stay `require_role(RoleLevel.SUPER_ADMIN)`-gated — no change to that
  boundary.
- No new PII surfaced: `last_active_at`/counts are aggregates, not raw
  user data; the staff roster already existed via `GET /admin/sessions`
  and already truncates `ip_address_hash` for display (it's a hash, not the
  raw IP, per this codebase's Tier 2 classification).

## File list

**Backend (modify):**
- `app/services/admin_service.py` — enrich `list_tenants`
- `app/schemas/admin.py` — extend `TenantSummary`
- `tests/unit/test_admin_service.py` — new aggregation tests

**Frontend (rewrite from scratch, not modify):**
- `app/templates/admin/index.html` — full rewrite, not a patch
- `static/js/admin-tenants.js` — already exists (the current tenant-list JS,
  confirmed via `admin/index.html`'s script tag); this sub-project replaces
  its content wholesale rather than extending it — same filename, entirely
  new implementation.

## Resolved during spec self-review

- `AuditLog.tenant_id` has a single-column index (`app/db/models/audit.py`);
  there is no composite `(tenant_id, created_at)` index. Given the small
  per-tenant/total row counts this product has today (matching the
  no-pagination scope decision above), this is sufficient — no migration
  needed for this sub-project. Worth revisiting only if audit log volume
  grows enough to matter, which is a future concern, not this one's.
