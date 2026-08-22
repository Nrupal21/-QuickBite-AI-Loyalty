# Super Admin Dashboard + Manager-Scoped View — Design Spec

Date: 2026-08-22
Status: approved for planning

## Problem

RBAC already has four role levels (`SUPER_ADMIN` / `OWNER` / `MANAGER` / `STAFF`,
`app/core/rbac.py`) and `app/api/v1/routers/admin.py` already exposes four
Super-Admin-only endpoints (list tenants, force-logout, audit logs, trigger
GMB sync). But there is **no Super Admin frontend at all** — no
`app/templates/admin/` directory — and the Manager role renders an identical
dashboard to Owner with no visibility differentiation, even though `GET
/auth/me` already returns the caller's `role` and `permissions` for exactly
this purpose.

This spec covers two sub-projects:

- **A — Super Admin dashboard**: new backend capabilities + new frontend screens.
- **B — Manager-scoped dashboard**: frontend-only visibility changes to the
  existing Owner dashboard.

Both extend the **existing** "liquid glass" system (`DESIGN.md`,
`static/css/dashboard.css`, the `qb-glass` grammar) rather than introducing a
new one — it is already documented, already mobile-first (fixed sidebar
≥900px / `qb-mobile-tabbar` below), and already carries the locked Doc 4
palette. Building a second visual language would violate the system's own
"one glass grammar" contract.

## Out of scope

- Impersonation / "log in as tenant" (explicitly declined during scoping —
  higher risk, needs its own security review).
- Re-skinning the existing Owner dashboard pages — they keep their current
  (already-shipped) glass styling untouched.
- Persisting API request-volume history beyond a rolling Redis window (no new
  Alembic migration in this spec — see "API request volume" below for why
  Redis-only is sufficient for v1).

## Sub-project A — Super Admin Dashboard

### A1. Backend capabilities (4, all `require_role(RoleLevel.SUPER_ADMIN)`)

All four follow the exact pattern already established in `admin_service.py`:
mutating actions run inside `rls.admin_bypass_context()` (cross-tenant reads
need it; `Tenant`/`AuditLog` inserts with `tenant_id=None` don't), every
mutation writes an `AuditLog` row, every action is `structlog`'d with no PII,
every mutating route gets a `@limiter.limit(...)`.

**1. Suspend / reactivate a tenant**
- `PATCH /admin/tenants/{tenant_id}/status` — body `{"is_active": bool}`.
- Service: flips `Tenant.is_active`. Reject if `tenant_id` not found (404,
  reuse `_TENANT_NOT_FOUND`).
- A suspended tenant's staff/owner logins must actually be blocked — enforce
  in `_resolve_local`/`_resolve_external` (`app/api/v1/dependencies/auth.py`)
  by loading `user.tenant_id`'s `Tenant.is_active` and 403'ing
  (`TENANT_SUSPENDED`) alongside the existing `is_active` user check, not
  just at the admin-panel layer. This is the one change outside
  `admin_service.py`/`admin.py`.
- AuditLog: `action="admin.tenant_suspended"` / `"admin.tenant_reactivated"`,
  `resource_type="tenant"`, `resource_id=tenant_id`.

**2. Plan / subscription override**
- `PATCH /admin/tenants/{tenant_id}/subscription` — body
  `{"plan_id": UUID | None, "status": Literal["trialing","active","past_due","canceled","paused"] | None, "trial_ends_at": datetime | None}`
  (all optional, only supplied fields change).
- Service: updates `Tenant.plan_id` (restaurant schema, RLS-exempt) and/or the
  matching `payment.subscriptions` row (needs `admin_bypass_context` — payment
  schema is reachable only via `app_payment_rw` per AGENTS.md, so this method
  must go through the same access path `billing_service.py` already uses,
  not raw `session.execute` — check that module's pattern before writing
  this one, in-plan).
- AuditLog: `action="admin.subscription_overridden"`, `resource_type="tenant"`,
  `event_metadata={"plan_id": ..., "status": ..., "reason": ...}` — `reason`
  becomes a required field on the request body (never a silent comp).

**3. System health metrics**
- `GET /admin/health-metrics` — read-only, no audit log needed (a GET with no
  side effect).
- Response: active tenant count, signups in last 24h/7d (`Tenant.created_at`
  — confirm `Base` carries a timestamp mixin before relying on it, in-plan),
  Celery queue depth (Redis `LLEN` on the Celery broker's queue key — reuse
  `cache_service`'s existing `_client`, add a raw `llen` helper if missing),
  count of tenants with `Subscription.status == "past_due"`.

**4. User / API monitors** — three read endpoints:
- `GET /admin/sessions?user_id=&tenant_id=` — lists `restaurant.sessions`
  rows (`user_id`, `ip_address_hash`, `user_agent`, `expires_at`, `revoked`),
  runs in `admin_bypass_context`. Force-logout reuses the existing
  `POST /admin/users/{user_id}/force-logout` — no new mutation here.
- `GET /admin/api-usage?tenant_id=` — request volume + rate-limit trips.
  **Tracking mechanism (new)**: a dependency (not global middleware — keeps
  it opt-in to authenticated routes only) that runs after `get_current_user`
  and does `cache_service.incr(f"api_calls:{tenant_id}:{yyyymmdd}", ttl=8*86400)`
  — an 8-day TTL keeps a rolling week visible without a new table. On a 429
  from SlowAPI, its exception handler (check `app/core/rate_limiter.py` for
  the existing handler, in-plan) additionally does
  `cache_service.incr(f"api_429:{tenant_id}:{yyyymmdd}", ttl=8*86400)`. The
  endpoint reads back the last 7 days of both counters per tenant. Redis-only
  by design — this is an operational signal, not an audit trail (audit_logs
  already covers accountability), so no migration is needed.
- `GET /admin/security-flags` — a read-only query joining: users with
  `failed_login_count > 0` or `locked_until` in the future; recent
  `stamp_logs.is_fraudulent = true` rows; tenants with ≥3
  `admin.force_logout` audit entries in 7 days. Pure read against existing
  columns, no new tracking.

### A2. Schemas (`app/schemas/admin.py`, extend existing file)

Add: `TenantStatusUpdateRequest`, `SubscriptionOverrideRequest`,
`SubscriptionOverrideResponse`, `HealthMetricsResponse`, `SessionSummary`,
`SessionListResponse`, `ApiUsageResponse` (per-day counts + trips, last 7
days), `SecurityFlagsResponse` (three lists: locked accounts, fraud flags,
force-logout clusters).

### A3. Frontend — `app/templates/admin/*.html`

New pages, new `pages.py` routes (`/admin`, `/admin/tenants/{id}`,
`/admin/audit-logs`, `/admin/monitors`), same **no-server-side-auth-check**
convention every existing dashboard page already uses (client fetches
`GET /auth/me`, redirects to `/login` if absent, redirects to `/dashboard`
if `role != SUPER_ADMIN` — every real mutation is still enforced by
`require_role(SUPER_ADMIN)` server-side regardless).

Shell: a **second instance** of the `qb-glass` dashboard shell pattern — own
sidebar (Tenants / Audit Logs / Monitors / Health) + own `qb-mobile-tabbar`,
not a reuse of the Owner sidebar's nav items. Same `qb-dash`/`qb-dash-mesh`/
`qb-glass` CSS classes and Tailwind config block as `dashboard/index.html` —
copy that page's `<head>` contract (Tailwind CDN + inline config carrying the
Doc 4 brand-* tokens + `dashboard.css`), don't invent new tokens. New pages:

- `admin/index.html` — tenants list (glass KPI strip: active tenants,
  suspended count, past-due count) + a ranked/filterable tenant table
  (search by subdomain/name), suspend/reactivate + plan-override actions
  inline.
- `admin/audit_logs.html` — filterable log viewer (tenant, action, date
  range) reusing `AuditLogFilters` as-is.
- `admin/monitors.html` — sessions list (force-logout inline), API usage
  sparkline-per-tenant (hand-rolled SVG, matching the "no charting
  dependency" convention in DESIGN.md's Chart component), security flags as
  three glass panels.

New `static/js/admin-*.js` files (one per page, matching the existing
per-page-script convention — `dashboard.js`, `loyalty-analytics.js`, etc.)
plus a shared `admin-shell.js` mirroring `dashboard-shell.js`'s structure
(same session-read pattern, same account-sheet, different nav item list).

## Sub-project B — Manager-Scoped Dashboard (frontend-only)

No backend change — RBAC already correctly scopes billing/team-invite/
GMB-connect to Owner (`require_role(RoleLevel.OWNER)` in `team.py`/
`billing.py`), so a Manager's *requests* already 403 server-side. This is
purely presentational: read `role` off the same `quickbite_staff_session`
object `dashboard-shell.js` already parses (populated from `GET /auth/me` at
login), and when `role === 'MANAGER'`, apply a `disabled` state (not
`display:none`) to Owner-only controls, plus a small "Owner only" affordance
(title attribute or inline glass tooltip — match existing tooltip patterns if
any exist in `dashboard.css`, otherwise a plain `title=` attribute is
sufficient for v1).

Touch points (verify each still matches current markup during
implementation, since `settings.js`/`settings.html` currently carry
unrelated in-flight branch-management work per git status):
- `dashboard/settings.html` + `settings.js` — team invite/remove buttons.
- `dashboard/billing.html` + `billing.js` — plan-change/checkout actions.
- `dashboard/google_profile.html` — GMB connect/disconnect actions.

Add one shared helper to `dashboard-shell.js` (e.g. `applyRoleGating()`)
that queries `[data-owner-only]` elements and disables them when the session
role isn't `OWNER`/`SUPER_ADMIN` — a declarative attribute the three pages'
markup opts into, rather than three copies of the same role check.

## Testing

- **Unit** (`tests/unit/test_admin_service.py`, extend existing coverage):
  one test per new service method per AGENTS.md's "every acceptance
  criterion" rule — suspend blocks login, reactivate restores it, plan
  override writes the right audit metadata, health metrics math, session
  list scoping, api-usage counter increments, security-flags query
  correctness. Twilio/SendGrid/etc. mocked per existing convention; Redis
  used real per AGENTS.md §7.
- **Integration**: `tests/integration/test_admin_routes.py` — every new route
  gets a 200 (Super Admin), 403 (Owner/Manager/Staff) pair at minimum.
- **Playwright** (`/qa` or direct `mcp__playwright__*` per this session's
  tooling): Super Admin can reach `/admin/*`, suspend a tenant, see it
  reflected; a Manager session sees billing/team controls rendered but
  disabled; a Staff session is unaffected (no change for Staff in this
  spec). Run at both a desktop and a mobile (<900px) viewport to confirm the
  glass shell's responsive breakpoint holds on the new admin screens.

## Security notes (AGENTS.md §3 checklist)

- Every new mutating endpoint: `require_role(SUPER_ADMIN)`, rate-limited,
  audit-logged, structlog'd without PII.
- Tenant suspension must be enforced at the auth-resolution layer
  (`api/v1/dependencies/auth.py`), not only hidden in the admin UI — a
  suspended tenant's existing unexpired JWTs must stop working.
- Subscription override requires a `reason` field — no silent comps, visible
  in the audit trail.
- No new PII columns; `api_calls`/`api_429` Redis keys are keyed by
  `tenant_id` (not user), consistent with TIER-1 (non-PII, config-like)
  classification.

## File list

**Backend (new/modified):**
- `app/api/v1/routers/admin.py` — 6 new routes
- `app/services/admin_service.py` — 6 new methods
- `app/schemas/admin.py` — new request/response models
- `app/api/v1/dependencies/auth.py` — tenant-suspended check
- `app/core/cache_service.py` — possible `llen` helper for queue depth
- `app/api/v1/dependencies/usage_tracking.py` — new dependency module for the API-usage counter
- `tests/unit/test_admin_service.py` (extend), `tests/integration/test_admin_routes.py` (new)

**Frontend (new):**
- `app/templates/admin/index.html`, `admin/audit_logs.html`, `admin/monitors.html`
- `static/js/admin-shell.js`, `admin-tenants.js`, `admin-audit-logs.js`, `admin-monitors.js`
- `app/api/v1/routers/pages.py` — 4 new page routes

**Frontend (modified, Manager gating only):**
- `static/js/dashboard-shell.js` — `applyRoleGating()` helper
- `app/templates/dashboard/settings.html`, `dashboard/billing.html`, `dashboard/google_profile.html` — `data-owner-only` attributes
- `static/js/settings.js`, `static/js/billing.js` — wire the new helper (coordinate with the unrelated in-flight branch-management changes already in these files per git status — additive, not a rewrite)

## Open questions for the implementation plan (not blocking spec approval)

- Exact `billing_service.py` access pattern for the `payment` schema, to
  mirror in the subscription-override method.
- Whether `Base` already carries `created_at` (needed for "signups in last
  24h/7d" on `Tenant`) — confirm before writing the health-metrics query.
- Existing tooltip/`title` convention in `dashboard.css`, if any, for the
  "Owner only" affordance.
