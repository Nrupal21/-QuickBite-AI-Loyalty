# Super Admin Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Super Admin a working frontend (`app/templates/admin/*`) over four new backend capabilities — suspend/reactivate a tenant, override a tenant's subscription, platform health metrics, and per-tenant/user API + session monitoring — extending the existing `qb-glass` design system rather than inventing a new one.

**Architecture:** Backend follows the exact pattern `admin_service.py`/`admin.py` already establish (`require_role(SUPER_ADMIN)`, `rls.admin_bypass_context()` for cross-tenant reads/writes, an `AuditLog` row per mutation, structlog with no PII). Frontend is a second instance of the `qb-glass` dashboard shell (own sidebar/tab-bar, no server-side auth check — client reads `GET /auth/me`, same convention every existing dashboard page already uses) with three new pages: tenants (list + suspend/reactivate + subscription override), audit logs (existing endpoint, new viewer), monitors (sessions + API usage + security flags).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, Alembic, Redis (`app/core/cache_service.py`), Jinja2, Tailwind CDN + hand-authored `qb-glass` CSS (`static/css/dashboard.css`), vanilla JS (no framework, matches every existing dashboard page).

**Spec:** `docs/superpowers/specs/2026-08-22-super-admin-manager-dashboards-design.md` (Sub-project A)

## Global Constraints

- Every mutating admin endpoint: `require_role(RoleLevel.SUPER_ADMIN)`, rate-limited via `@limiter.limit(...)`, writes an `AuditLog` row, structlog'd with **no PII** (AGENTS.md §3, §6; CLAUDE.md).
- Never `f"SELECT ... {id}"` string-built SQL — always parameterized/ORM (AGENTS.md §3).
- Never `session.query()` — SQLAlchemy 2.x async `select()` + `await session.execute()` only (AGENTS.md §4).
- Error responses always `{"error": {"code": ..., "message": ...}}` shape (AGENTS.md §5).
- Every Celery-adjacent or Redis call that isn't the request's core purpose (the API-usage counters) must be **best-effort** — wrapped so a Redis hiccup never fails the underlying request.
- No new Python package without checking `requirements/*.txt` first (CLAUDE.md) — this plan introduces none.
- Frontend: extend `qb-glass` (`static/css/dashboard.css`, `DESIGN.md`) — no new color, no new blur recipe, no colored `border-left` accents, no uniform same-size icon-card grids (DESIGN.md's Do's/Don'ts).
- `pytest` in this repo requires `-p no:langsmith_plugin` to collect (this session's own project memory).

---

## Part 1 — Backend

### Task 1: Migration — grant `quickbite_admin_bypass` access to the `payment` schema

**Files:**
- Create: `app/db/migrations/versions/0017_admin_bypass_payment_grant.py`

**Interfaces:**
- Produces: `quickbite_admin_bypass` role gains `USAGE ON SCHEMA payment` + `SELECT, UPDATE ON payment.subscriptions` — required by Task 4's subscription-override method and Task 5's past-due-count health metric, both of which run inside `rls.admin_bypass_context()`.

- [ ] **Step 1: Write the migration, following migration 0015's exact precedent**

```python
"""Grant quickbite_admin_bypass access to payment.subscriptions

ADMIN-01's Super Admin panel needs two new capabilities that read/write
payment.subscriptions cross-tenant: overriding a tenant's plan/status
(admin_service.override_subscription) and counting past-due subscriptions
for the health-metrics endpoint (admin_service.get_health_metrics).

Migration 0010 granted quickbite_admin_bypass USAGE on the restaurant
schema only. BYPASSRLS bypasses row-level security policies, not
table-level GRANTs (migration 0007's own docstring makes this point) —
the payment schema was granted only to app_payment_rw (migration 0002),
so quickbite_admin_bypass currently gets "permission denied for schema
payment" the moment it touches payment.subscriptions, RLS bypass or not.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-22
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_BYPASS_ROLE = "quickbite_admin_bypass"


def upgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"GRANT USAGE ON SCHEMA payment TO {ADMIN_BYPASS_ROLE}; "
        f"GRANT SELECT, UPDATE ON payment.subscriptions TO {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"REVOKE SELECT, UPDATE ON payment.subscriptions FROM {ADMIN_BYPASS_ROLE}; "
        f"REVOKE USAGE ON SCHEMA payment FROM {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )
```

Confirm `down_revision = "0016"` still matches the latest migration on disk before saving — if a newer migration landed since this plan was written (check with Step 2 below), update `down_revision` and this file's own `depends_on` chain accordingly.

- [ ] **Step 2: Verify migration ordering**

Run: `ls app/db/migrations/versions/ | sort | tail -3`
Expected: `0017_admin_bypass_payment_grant.py` is now the newest file, immediately after `0016_whatsapp_marketing.py`.

- [ ] **Step 3: Apply the migration**

Run: `alembic upgrade head`
Expected: no errors. If `quickbite_admin_bypass` doesn't exist in this environment (e.g. a fresh local DB that never ran migration 0010), the `IF EXISTS` guard makes this a no-op — expected and fine.

- [ ] **Step 4: Commit**

```bash
git add app/db/migrations/versions/0017_admin_bypass_payment_grant.py
git commit -m "feat(admin): grant quickbite_admin_bypass access to payment schema

Needed for the upcoming subscription-override and health-metrics
Super Admin capabilities, which read/write payment.subscriptions
cross-tenant under admin_bypass_context. BYPASSRLS alone doesn't
grant table access — migration 0015 established this exact pattern
for restaurant.identity_links; this does the same for payment."
```

---

### Task 2: New schemas for all four Super Admin capabilities

**Files:**
- Modify: `app/schemas/admin.py` (append to existing file)

**Interfaces:**
- Consumes: nothing new (pure Pydantic models).
- Produces: `TenantStatusUpdateRequest`, `TenantStatusUpdateResponse`, `SubscriptionOverrideRequest`, `SubscriptionOverrideResponse`, `HealthMetricsResponse`, `SessionSummary`, `SessionListResponse`, `ApiUsageDay`, `ApiUsageResponse`, `LockedAccountFlag`, `FraudFlag`, `ForceLogoutCluster`, `SecurityFlagsResponse` — consumed by Tasks 3, 4, 5, 6, 7, 8's service methods and routes.

- [ ] **Step 1: Append the new schemas**

Add to the end of `app/schemas/admin.py` (the file already has `import uuid`, `from datetime import datetime`, `from typing import Literal`, `from pydantic import BaseModel, Field` at the top — no new imports needed):

```python
class TenantStatusUpdateRequest(BaseModel):
    is_active: bool


class TenantStatusUpdateResponse(BaseModel):
    tenant_id: uuid.UUID
    is_active: bool


class SubscriptionOverrideRequest(BaseModel):
    """All fields but `reason` are optional — only supplied fields change.
    `reason` is required on every call so a plan comp always has a stated
    justification in the audit trail, never a silent change."""

    plan_id: uuid.UUID | None = None
    status: Literal["trialing", "active", "past_due", "canceled", "paused"] | None = None
    trial_ends_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=500)


class SubscriptionOverrideResponse(BaseModel):
    tenant_id: uuid.UUID
    plan_id: uuid.UUID | None
    status: str
    trial_ends_at: datetime | None


class HealthMetricsResponse(BaseModel):
    active_tenants: int
    suspended_tenants: int
    signups_last_24h: int
    signups_last_7d: int
    past_due_subscriptions: int
    queue_depth: int


class SessionSummary(BaseModel):
    session_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    ip_address_hash: str
    user_agent: str
    expires_at: datetime
    revoked: bool


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]


class ApiUsageDay(BaseModel):
    date: str  # YYYY-MM-DD
    request_count: int
    rate_limited_count: int


class ApiUsageResponse(BaseModel):
    tenant_id: uuid.UUID
    days: list[ApiUsageDay]


class LockedAccountFlag(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    failed_login_count: int
    locked_until: datetime | None


class FraudFlag(BaseModel):
    stamp_log_id: uuid.UUID
    tenant_id: uuid.UUID | None
    branch_id: uuid.UUID | None
    scanned_at: datetime


class ForceLogoutCluster(BaseModel):
    tenant_id: uuid.UUID | None
    count: int


class SecurityFlagsResponse(BaseModel):
    locked_accounts: list[LockedAccountFlag]
    fraud_flags: list[FraudFlag]
    force_logout_clusters: list[ForceLogoutCluster]
```

- [ ] **Step 2: Confirm the module still imports cleanly**

Run: `python -c "import app.schemas.admin"`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add app/schemas/admin.py
git commit -m "feat(admin): add schemas for tenant status, subscription override, health metrics, and user/API monitors"
```

---

### Task 3: Suspend/reactivate a tenant, enforced at auth resolution

**Files:**
- Modify: `app/services/admin_service.py` (add method)
- Modify: `app/api/v1/routers/admin.py` (add route)
- Modify: `app/api/v1/dependencies/auth.py` (enforce suspension in `_resolve_local`)
- Modify: `app/services/identity_link_service.py` (enforce suspension in `_principal_from_link`)
- Test: `tests/unit/test_admin_service.py`, `tests/unit/test_rbac.py`, `tests/unit/test_identity_link_service.py`

**Interfaces:**
- Consumes: `TenantStatusUpdateRequest`/`TenantStatusUpdateResponse` (Task 2), `_TENANT_NOT_FOUND` (already in `admin_service.py`).
- Produces: `AdminService.set_tenant_status(tenant_id: uuid.UUID, is_active: bool, admin: User) -> TenantStatusUpdateResponse` — no other task depends on this method directly, but the enforcement half (auth.py/identity_link_service.py changes) affects every authenticated request platform-wide, so this task must land cleanly before any other task's manual verification.

- [ ] **Step 1: Write the failing service test**

Add to `tests/unit/test_admin_service.py` (uses the file's existing `make_session`/`make_result`/`added`/`make_admin`/`make_tenant` helpers and `_mock_admin_bypass` autouse fixture):

```python
# --- set_tenant_status -------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_tenant_flips_is_active_and_audit_logs():
    admin = make_admin()
    tenant = make_tenant(is_active=True)
    session = make_session([tenant])

    response = await AdminService(session=session).set_tenant_status(
        tenant.id, is_active=False, admin=admin
    )

    assert response.is_active is False
    assert tenant.is_active is False
    entry = added(session, AuditLog)[-1]
    assert entry.action == "admin.tenant_suspended"
    assert entry.resource_type == "tenant"
    assert entry.resource_id == tenant.id
    assert entry.tenant_id is None


@pytest.mark.asyncio
async def test_reactivate_tenant_audit_logs_the_right_action():
    admin = make_admin()
    tenant = make_tenant(is_active=False)
    session = make_session([tenant])

    response = await AdminService(session=session).set_tenant_status(
        tenant.id, is_active=True, admin=admin
    )

    assert response.is_active is True
    entry = added(session, AuditLog)[-1]
    assert entry.action == "admin.tenant_reactivated"


@pytest.mark.asyncio
async def test_set_tenant_status_unknown_tenant_returns_404():
    admin = make_admin()
    session = make_session([None])

    with pytest.raises(HTTPException) as exc_info:
        await AdminService(session=session).set_tenant_status(
            uuid.uuid4(), is_active=False, admin=admin
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "TENANT_NOT_FOUND"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_admin_service.py -k tenant_status -p no:langsmith_plugin -v`
Expected: FAIL — `AttributeError: 'AdminService' object has no attribute 'set_tenant_status'`

- [ ] **Step 3: Add the schema imports and the service method**

In `app/schemas/admin.py`'s import at the top of `admin_service.py`, extend the existing `from app.schemas.admin import (...)` block to also import `TenantStatusUpdateRequest, TenantStatusUpdateResponse` (request schema imported for type-hint completeness even though the route will unpack it — matches this file's existing style of importing every schema it touches).

Add to `AdminService` in `app/services/admin_service.py`:

```python
    async def set_tenant_status(
        self, tenant_id: uuid.UUID, is_active: bool, admin: User
    ) -> TenantStatusUpdateResponse:
        result = await self.session.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            raise _TENANT_NOT_FOUND

        tenant.is_active = is_active

        self.session.add(
            AuditLog(
                tenant_id=None,
                user_id=admin.id,
                action="admin.tenant_suspended" if not is_active else "admin.tenant_reactivated",
                resource_type="tenant",
                resource_id=tenant.id,
                event_metadata=None,
            )
        )
        await self.session.commit()

        logger.info(
            "admin.tenant_status_changed",
            admin_id=str(admin.id),
            tenant_id=str(tenant.id),
            is_active=is_active,
        )
        return TenantStatusUpdateResponse(tenant_id=tenant.id, is_active=is_active)
```

No `admin_bypass_context` needed here — `restaurant.tenants` is RLS-exempt (migration 0009), same reason `list_tenants` doesn't use it.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_admin_service.py -k tenant_status -p no:langsmith_plugin -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the route**

In `app/api/v1/routers/admin.py`, extend the `from app.schemas.admin import (...)` block with `TenantStatusUpdateRequest, TenantStatusUpdateResponse`, and add:

```python
@router.patch(
    "/tenants/{tenant_id}/status",
    response_model=TenantStatusUpdateResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("30/hour")
async def update_tenant_status(
    request: Request,
    tenant_id: uuid.UUID,
    payload: TenantStatusUpdateRequest,
    current_user: User = Depends(require_role(RoleLevel.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> TenantStatusUpdateResponse:
    return await AdminService(session=session).set_tenant_status(
        tenant_id, payload.is_active, current_user
    )
```

- [ ] **Step 6: Write the failing enforcement test in test_rbac.py**

Add near the other `get_current_user` tests in `tests/unit/test_rbac.py` (needs a new import `from app.db.models.tenant import Tenant` at the top of the file):

```python
@pytest.mark.asyncio
async def test_suspended_tenant_returns_403_even_with_valid_token(mocker):
    """A suspended tenant's already-issued, still-unexpired JWT must stop
    working immediately — suspension is enforced here, not only hidden in
    the admin UI."""
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False)
    )
    user = make_user()
    token = create_access_token(user.id, TENANT_ID, "OWNER")
    # set_config, select(User), select(Tenant.is_active) — in that order.
    session = make_session([None, user, False])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_request(token), session)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "TENANT_SUSPENDED"
```

Also update the existing `test_sets_app_tenant_id_on_the_db_session` (same file) — it now needs a third queued result for the new tenant-status query it will pass through:

```python
    user = make_user()
    token = create_access_token(user.id, TENANT_ID, "OWNER")
    session = make_session([None, user, True])
```

(was `make_session([None, user])` — the third value is the new `Tenant.is_active` query's result, `True` so the test's existing assertions still reach the end of the function.)

- [ ] **Step 7: Run to verify the new test fails and the updated one still fails until Step 8 lands**

Run: `pytest tests/unit/test_rbac.py -k "suspended_tenant or sets_app_tenant_id" -p no:langsmith_plugin -v`
Expected: `test_suspended_tenant_returns_403_even_with_valid_token` FAILS (no such check exists yet); `test_sets_app_tenant_id_on_the_db_session` FAILS with a mock `StopAsyncIteration`-style error from the extra queued value not being consumed (confirms the test is now correctly primed for the code change).

- [ ] **Step 8: Enforce tenant suspension in `_resolve_local`**

In `app/api/v1/dependencies/auth.py`, add the import `from app.db.models.tenant import Tenant` alongside the existing `from app.db.models.user import Role, User`, and add a new exception constant near `_ACCOUNT_DEACTIVATED`:

```python
_TENANT_SUSPENDED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "error": {
            "code": "TENANT_SUSPENDED",
            "message": "This restaurant's account has been suspended. Contact support.",
        }
    },
)
```

In `_resolve_local`, change:

```python
    if not user.is_active:
        raise _ACCOUNT_DEACTIVATED

    _assert_not_globally_revoked(user, claims)
```

to:

```python
    if not user.is_active:
        raise _ACCOUNT_DEACTIVATED

    if user.tenant_id is not None:
        tenant_result = await session.execute(
            select(Tenant.is_active).where(Tenant.id == user.tenant_id)
        )
        if tenant_result.scalar_one_or_none() is False:
            raise _TENANT_SUSPENDED

    _assert_not_globally_revoked(user, claims)
```

`restaurant.tenants` is RLS-exempt (migration 0009), so this query needs no tenant context or bypass — it runs safely regardless of what's already bound on `session`.

- [ ] **Step 9: Run to verify both tests now pass**

Run: `pytest tests/unit/test_rbac.py -p no:langsmith_plugin -v`
Expected: PASS, every test in the file (confirms Step 8's change didn't break any other `get_current_user`/`require_role` test).

- [ ] **Step 10: Mirror the same enforcement for external (Supabase/Firebase) tokens**

In `app/services/identity_link_service.py`, add `from app.db.models.tenant import Tenant` to the imports, and in `_principal_from_link`, change:

```python
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "ACCOUNT_DEACTIVATED",
                        "message": "This account has been deactivated. "
                        "Contact your restaurant owner.",
                    }
                },
            )
        return Principal(
```

to:

```python
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "ACCOUNT_DEACTIVATED",
                        "message": "This account has been deactivated. "
                        "Contact your restaurant owner.",
                    }
                },
            )
        if user.tenant_id is not None:
            tenant_result = await session.execute(
                select(Tenant.is_active).where(Tenant.id == user.tenant_id)
            )
            if tenant_result.scalar_one_or_none() is False:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": {
                            "code": "TENANT_SUSPENDED",
                            "message": "This restaurant's account has been suspended. "
                            "Contact support.",
                        }
                    },
                )
        return Principal(
```

- [ ] **Step 11: Update the one identity-link test this reaches and confirm the rest still pass**

In `tests/unit/test_identity_link_service.py`, `test_resolve_returns_principal_for_active_user_link` needs a third queued result:

```python
    session = make_session([link, user, True])
```

(was `make_session([link, user])`.)

Run: `pytest tests/unit/test_identity_link_service.py -p no:langsmith_plugin -v`
Expected: PASS, every test in the file.

- [ ] **Step 12: Run the full admin_service suite**

Run: `pytest tests/unit/test_admin_service.py -p no:langsmith_plugin -v`
Expected: PASS, every test in the file.

- [ ] **Step 13: Commit**

```bash
git add app/services/admin_service.py app/api/v1/routers/admin.py \
        app/api/v1/dependencies/auth.py app/services/identity_link_service.py \
        tests/unit/test_admin_service.py tests/unit/test_rbac.py \
        tests/unit/test_identity_link_service.py
git commit -m "feat(admin): suspend/reactivate a tenant, enforced at auth resolution

PATCH /admin/tenants/{id}/status flips Tenant.is_active. Enforcement
lives at the point every authenticated request already resolves its
principal (_resolve_local for local JWTs, _principal_from_link for
Supabase/Firebase) so a suspended tenant's already-issued, unexpired
tokens stop working immediately — not just hidden in the admin UI."
```

---

### Task 4: Subscription/plan override

**Files:**
- Modify: `app/services/admin_service.py`, `app/api/v1/routers/admin.py`
- Test: `tests/unit/test_admin_service.py`

**Interfaces:**
- Consumes: `SubscriptionOverrideRequest`/`SubscriptionOverrideResponse` (Task 2), `rls.admin_bypass_context` (existing), `payment.subscriptions` access granted in Task 1.
- Produces: `AdminService.override_subscription(tenant_id, payload: SubscriptionOverrideRequest, admin: User) -> SubscriptionOverrideResponse`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_admin_service.py`:

```python
from app.db.models.subscription import Subscription
from app.schemas.admin import SubscriptionOverrideRequest


def make_subscription(**overrides) -> Subscription:
    defaults = {
        "tenant_id": OTHER_TENANT_ID,
        "plan_id": uuid.uuid4(),
        "status": "active",
        "provider": "razorpay",
        "current_period_end": datetime(2026, 12, 31, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    sub = Subscription(**defaults)
    sub.id = uuid.uuid4()
    return sub


# --- override_subscription --------------------------------------------------


@pytest.mark.asyncio
async def test_override_subscription_updates_status_and_audit_logs_reason():
    admin = make_admin()
    sub = make_subscription(status="active")
    session = make_session([sub])
    payload = SubscriptionOverrideRequest(status="canceled", reason="Customer requested via support ticket #4821")

    response = await AdminService(session=session).override_subscription(
        OTHER_TENANT_ID, payload, admin
    )

    assert response.status == "canceled"
    assert sub.status == "canceled"
    entry = added(session, AuditLog)[-1]
    assert entry.action == "admin.subscription_overridden"
    assert entry.event_metadata["reason"] == "Customer requested via support ticket #4821"
    assert entry.event_metadata["status"] == "canceled"


@pytest.mark.asyncio
async def test_override_subscription_updates_plan_and_trial_end():
    admin = make_admin()
    sub = make_subscription()
    new_plan_id = uuid.uuid4()
    trial_end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    session = make_session([sub])
    payload = SubscriptionOverrideRequest(
        plan_id=new_plan_id, trial_ends_at=trial_end, reason="Comped for beta partner"
    )

    response = await AdminService(session=session).override_subscription(
        OTHER_TENANT_ID, payload, admin
    )

    assert response.plan_id == new_plan_id
    assert sub.plan_id == new_plan_id
    assert sub.trial_ends_at == trial_end


@pytest.mark.asyncio
async def test_override_subscription_no_subscription_returns_404():
    admin = make_admin()
    session = make_session([None])
    payload = SubscriptionOverrideRequest(status="active", reason="test")

    with pytest.raises(HTTPException) as exc_info:
        await AdminService(session=session).override_subscription(uuid.uuid4(), payload, admin)

    assert exc_info.value.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_admin_service.py -k override_subscription -p no:langsmith_plugin -v`
Expected: FAIL — no such method / no such import yet.

- [ ] **Step 3: Implement**

Add a new not-found constant and the method to `app/services/admin_service.py`. Extend the top-of-file imports: `from app.db.models.subscription import Subscription` and add `SubscriptionOverrideRequest, SubscriptionOverrideResponse` to the existing `from app.schemas.admin import (...)` block.

```python
_SUBSCRIPTION_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail={
        "error": {
            "code": "SUBSCRIPTION_NOT_FOUND",
            "message": "This tenant has no subscription to override.",
        }
    },
)
```

```python
    async def override_subscription(
        self, tenant_id: uuid.UUID, payload: SubscriptionOverrideRequest, admin: User
    ) -> SubscriptionOverrideResponse:
        async with rls.admin_bypass_context(self.session):
            result = await self.session.execute(
                select(Subscription).where(Subscription.tenant_id == tenant_id)
            )
            sub = result.scalar_one_or_none()
            if sub is None:
                raise _SUBSCRIPTION_NOT_FOUND

            if payload.plan_id is not None:
                sub.plan_id = payload.plan_id
            if payload.status is not None:
                sub.status = payload.status
            if payload.trial_ends_at is not None:
                sub.trial_ends_at = payload.trial_ends_at

            self.session.add(
                AuditLog(
                    tenant_id=None,
                    user_id=admin.id,
                    action="admin.subscription_overridden",
                    resource_type="tenant",
                    resource_id=tenant_id,
                    event_metadata={
                        "plan_id": str(payload.plan_id) if payload.plan_id else None,
                        "status": payload.status,
                        "trial_ends_at": payload.trial_ends_at.isoformat()
                        if payload.trial_ends_at
                        else None,
                        "reason": payload.reason,
                    },
                )
            )
            await self.session.commit()

        logger.info(
            "admin.subscription_overridden",
            admin_id=str(admin.id),
            tenant_id=str(tenant_id),
        )
        return SubscriptionOverrideResponse(
            tenant_id=tenant_id,
            plan_id=sub.plan_id,
            status=sub.status,
            trial_ends_at=sub.trial_ends_at,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_admin_service.py -p no:langsmith_plugin -v`
Expected: PASS, every test in the file.

- [ ] **Step 5: Add the route**

Extend `admin.py`'s schema import with `SubscriptionOverrideRequest, SubscriptionOverrideResponse`, add:

```python
@router.patch(
    "/tenants/{tenant_id}/subscription",
    response_model=SubscriptionOverrideResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("30/hour")
async def override_tenant_subscription(
    request: Request,
    tenant_id: uuid.UUID,
    payload: SubscriptionOverrideRequest,
    current_user: User = Depends(require_role(RoleLevel.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> SubscriptionOverrideResponse:
    return await AdminService(session=session).override_subscription(
        tenant_id, payload, current_user
    )
```

- [ ] **Step 6: Commit**

```bash
git add app/services/admin_service.py app/api/v1/routers/admin.py tests/unit/test_admin_service.py
git commit -m "feat(admin): allow Super Admin to override a tenant's plan/subscription status

Requires a reason on every call (schema-enforced) so a comp or manual
plan change always has a stated justification in the audit trail."
```

---

### Task 5: System health metrics

**Files:**
- Modify: `app/core/cache_service.py` (add `llen` helper)
- Modify: `app/services/admin_service.py`, `app/api/v1/routers/admin.py`
- Test: `tests/unit/test_admin_service.py`

**Interfaces:**
- Consumes: `HealthMetricsResponse` (Task 2), `payment.subscriptions` access (Task 1).
- Produces: `cache_service.queue_depth() -> int`, `AdminService.get_health_metrics() -> HealthMetricsResponse`.

- [ ] **Step 1: Add the Redis queue-depth helper**

Add to `app/core/cache_service.py`, alongside the existing `incr`/`exists` functions:

```python
async def queue_depth(queue_name: str = "celery") -> int:
    """Pending-task count for a Celery Redis-transport queue — LLEN on the
    queue's own list key, which is how Celery's default Redis transport
    stores an undelivered task. `celery_app.py` sets no custom queue name,
    so "celery" is the one queue this app ever uses."""
    return await _client.llen(queue_name)
```

- [ ] **Step 2: Write the failing service test**

Add to `tests/unit/test_admin_service.py`:

```python
# --- get_health_metrics ------------------------------------------------------


@pytest.mark.asyncio
async def test_health_metrics_aggregates_all_six_figures(mocker):
    admin = make_admin()
    mocker.patch(
        "app.services.admin_service.cache_service.queue_depth", AsyncMock(return_value=7)
    )
    # Query order: active_tenants count, suspended_tenants count,
    # signups_24h count, signups_7d count, past_due_subscriptions count.
    session = make_session([12, 3, 1, 4, 2])

    response = await AdminService(session=session).get_health_metrics()

    assert response.active_tenants == 12
    assert response.suspended_tenants == 3
    assert response.signups_last_24h == 1
    assert response.signups_last_7d == 4
    assert response.past_due_subscriptions == 2
    assert response.queue_depth == 7
```

This test needs `make_result`'s `scalar_one_or_none` to return a plain int for a `func.count()` query — confirm `make_result` in the file already supports that (it does: `result.scalar_one_or_none.return_value = value` works for any value, count included).

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/unit/test_admin_service.py -k health_metrics -p no:langsmith_plugin -v`
Expected: FAIL — no such method yet.

- [ ] **Step 4: Implement**

Add imports to `admin_service.py`: `from datetime import datetime, timedelta, timezone`, `from sqlalchemy import func`, `from app.core import cache_service`, and add `HealthMetricsResponse` to the schema import block.

```python
    async def get_health_metrics(self) -> HealthMetricsResponse:
        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)

        active_result = await self.session.execute(
            select(func.count()).select_from(Tenant).where(Tenant.is_active.is_(True))
        )
        suspended_result = await self.session.execute(
            select(func.count()).select_from(Tenant).where(Tenant.is_active.is_(False))
        )
        signups_24h_result = await self.session.execute(
            select(func.count()).select_from(Tenant).where(Tenant.created_at >= day_ago)
        )
        signups_7d_result = await self.session.execute(
            select(func.count()).select_from(Tenant).where(Tenant.created_at >= week_ago)
        )
        # payment.subscriptions is RLS-protected and only quickbite_admin_bypass
        # (Task 1's grant) can read it cross-tenant.
        async with rls.admin_bypass_context(self.session):
            past_due_result = await self.session.execute(
                select(func.count()).select_from(Subscription).where(Subscription.status == "past_due")
            )

        return HealthMetricsResponse(
            active_tenants=active_result.scalar_one_or_none() or 0,
            suspended_tenants=suspended_result.scalar_one_or_none() or 0,
            signups_last_24h=signups_24h_result.scalar_one_or_none() or 0,
            signups_last_7d=signups_7d_result.scalar_one_or_none() or 0,
            past_due_subscriptions=past_due_result.scalar_one_or_none() or 0,
            queue_depth=await cache_service.queue_depth(),
        )
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/unit/test_admin_service.py -p no:langsmith_plugin -v`
Expected: PASS, every test.

- [ ] **Step 6: Add the route**

Extend `admin.py`'s schema import with `HealthMetricsResponse`, add:

```python
@router.get(
    "/health-metrics", response_model=HealthMetricsResponse, status_code=status.HTTP_200_OK
)
async def get_health_metrics(
    current_user: User = Depends(require_role(RoleLevel.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> HealthMetricsResponse:
    return await AdminService(session=session).get_health_metrics()
```

No rate limit — a read-only GET with no mutation, same as `list_tenants`/`list_audit_logs`, neither of which is rate-limited today.

- [ ] **Step 7: Commit**

```bash
git add app/core/cache_service.py app/services/admin_service.py app/api/v1/routers/admin.py tests/unit/test_admin_service.py
git commit -m "feat(admin): add GET /admin/health-metrics — platform-wide operational snapshot"
```

---

### Task 6: Active sessions list

**Files:**
- Modify: `app/services/admin_service.py`, `app/api/v1/routers/admin.py`
- Test: `tests/unit/test_admin_service.py`

**Interfaces:**
- Consumes: `SessionSummary`/`SessionListResponse` (Task 2), `restaurant.sessions` (`app/db/models/user.py`'s `Session` model — already exists).
- Produces: `AdminService.list_sessions(tenant_id: uuid.UUID | None, user_id: uuid.UUID | None) -> SessionListResponse`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_admin_service.py`:

```python
from app.db.models.user import Session as UserSession


def make_user_session(**overrides) -> UserSession:
    defaults = {
        "user_id": uuid.uuid4(),
        "tenant_id": OTHER_TENANT_ID,
        "refresh_token_hash": "irrelevant",
        "ip_address_hash": "irrelevant-hash",
        "user_agent": "Mozilla/5.0",
        "expires_at": datetime(2026, 12, 31, tzinfo=timezone.utc),
        "revoked": False,
    }
    defaults.update(overrides)
    row = UserSession(**defaults)
    row.id = uuid.uuid4()
    return row


# --- list_sessions ------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_returns_every_session_cross_tenant():
    rows = [make_user_session(), make_user_session(tenant_id=ADMIN_TENANT_ID)]
    session = make_session([rows])

    response = await AdminService(session=session).list_sessions(tenant_id=None, user_id=None)

    assert len(response.sessions) == 2


@pytest.mark.asyncio
async def test_list_sessions_filters_by_tenant():
    rows = [make_user_session(tenant_id=OTHER_TENANT_ID)]
    session = make_session([rows])

    response = await AdminService(session=session).list_sessions(
        tenant_id=OTHER_TENANT_ID, user_id=None
    )

    assert len(response.sessions) == 1
    query = session.execute.await_args.args[0]
    assert "sessions.tenant_id" in str(query.whereclause)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_admin_service.py -k list_sessions -p no:langsmith_plugin -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add `from app.db.models.user import Session as UserSession` and `SessionSummary, SessionListResponse` (schema import) to `admin_service.py`.

```python
    async def list_sessions(
        self, tenant_id: uuid.UUID | None, user_id: uuid.UUID | None
    ) -> SessionListResponse:
        async with rls.admin_bypass_context(self.session):
            query = select(UserSession)
            if tenant_id is not None:
                query = query.where(UserSession.tenant_id == tenant_id)
            if user_id is not None:
                query = query.where(UserSession.user_id == user_id)
            query = query.order_by(UserSession.expires_at.desc())
            result = await self.session.execute(query)
            rows = result.scalars().all()

        return SessionListResponse(
            sessions=[
                SessionSummary(
                    session_id=row.id,
                    user_id=row.user_id,
                    tenant_id=row.tenant_id,
                    ip_address_hash=row.ip_address_hash,
                    user_agent=row.user_agent,
                    expires_at=row.expires_at,
                    revoked=row.revoked,
                )
                for row in rows
            ]
        )
```

`restaurant.sessions` needs the same `quickbite_admin_bypass` grant `users`/`sessions` already got in migration 0010 (per that migration's own docstring, referenced by `test_admin_service.py`'s file header) — confirm this by re-reading migration 0010's `upgrade()` before running the DB-backed step below; if `sessions` isn't in its grant list, add a follow-up migration mirroring Task 1's pattern rather than assuming.

- [ ] **Step 4: Confirm migration 0010 already grants `sessions`**

Run: view `app/db/migrations/versions/0010_admin_bypass_role.py`'s `upgrade()` function.
Expected: a `GRANT ... ON restaurant.sessions TO quickbite_admin_bypass` line is present. If it is not, create `0018_admin_bypass_sessions_grant.py` following Task 1's exact pattern (`GRANT SELECT ON restaurant.sessions TO quickbite_admin_bypass;`) before continuing — force-logout already reads `sessions` via `AuthService.logout_all` inside this same bypass context, so if that grant were missing, `force_logout_user` would already be broken; treat a missing grant here as a signal to re-check that assumption, not as new work this plan invented.

- [ ] **Step 5: Run to verify the unit test passes**

Run: `pytest tests/unit/test_admin_service.py -p no:langsmith_plugin -v`
Expected: PASS, every test.

- [ ] **Step 6: Add the route**

Extend `admin.py`'s schema import with `SessionListResponse`, add:

```python
@router.get(
    "/sessions", response_model=SessionListResponse, status_code=status.HTTP_200_OK
)
async def list_sessions(
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    current_user: User = Depends(require_role(RoleLevel.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    return await AdminService(session=session).list_sessions(tenant_id, user_id)
```

- [ ] **Step 7: Commit**

```bash
git add app/services/admin_service.py app/api/v1/routers/admin.py tests/unit/test_admin_service.py
git commit -m "feat(admin): add GET /admin/sessions — cross-tenant active session listing

Force-logout (already shipped) is the mutation; this is the read side
that lets Super Admin find which session/user to act on."
```

---

### Task 7: API request-volume and rate-limit-trip tracking

**Files:**
- Modify: `app/api/v1/dependencies/auth.py` (instrument `get_current_user`)
- Modify: `app/core/rate_limiter.py` (custom 429 handler)
- Modify: `app/main.py` (register the new handler)
- Modify: `app/services/admin_service.py`, `app/api/v1/routers/admin.py`
- Test: `tests/unit/test_rbac.py`, `tests/unit/test_admin_service.py`

**Interfaces:**
- Consumes: `cache_service.incr` (existing), `ApiUsageResponse`/`ApiUsageDay` (Task 2).
- Produces: two new Redis key families (`api_calls:{tenant_id}:{yyyymmdd}`, `api_429:{tenant_id}:{yyyymmdd}`, both 8-day TTL) and `AdminService.get_api_usage(tenant_id) -> ApiUsageResponse`.

This is the one capability with no persisted DB table — it's deliberately
Redis-only (an 8-day rolling window), since it's an operational signal, not
an audit trail (`audit_logs` already covers accountability — see spec).

- [ ] **Step 1: Write the failing test for request-volume tracking**

Add to `tests/unit/test_rbac.py`:

```python
@pytest.mark.asyncio
async def test_get_current_user_increments_api_call_counter(mocker):
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False)
    )
    incr = mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.incr", AsyncMock(return_value=1)
    )
    user = make_user()
    token = create_access_token(user.id, TENANT_ID, "OWNER")
    session = make_session([None, user, True])

    await get_current_user(make_request(token), session)

    incr.assert_awaited_once()
    key = incr.await_args.args[0]
    assert key.startswith(f"api_calls:{TENANT_ID}:")


@pytest.mark.asyncio
async def test_get_current_user_survives_redis_outage_during_counting(mocker):
    """A Redis hiccup on the analytics counter must never fail the actual
    request — it's a nice-to-have, not a correctness dependency."""
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.exists", AsyncMock(return_value=False)
    )
    mocker.patch(
        "app.api.v1.dependencies.auth.cache_service.incr",
        AsyncMock(side_effect=ConnectionError("redis down")),
    )
    user = make_user()
    token = create_access_token(user.id, TENANT_ID, "OWNER")
    session = make_session([None, user, True])

    returned = await get_current_user(make_request(token), session)

    assert returned is user
```

- [ ] **Step 2: Run to verify both fail**

Run: `pytest tests/unit/test_rbac.py -k api_call_counter -p no:langsmith_plugin -v`
Expected: FAIL (no counting happens yet).

- [ ] **Step 3: Instrument `get_current_user`**

In `app/api/v1/dependencies/auth.py`, add `from datetime import date` is unnecessary (already imports `datetime`) — use `datetime.now(timezone.utc).strftime("%Y%m%d")`. Change:

```python
async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> User:
    """The authenticated staff/owner User. 403 for a customer principal.

    Signature is deliberately unchanged (`request` first, positional) — routes
    and the AUTH-04 tests call it directly.
    """
    principal = await resolve_principal(request, session)
    if principal.subject_type is not SubjectType.USER or principal.user is None:
        # A customer token is a valid credential for the wrong surface. 403,
        # not 401: re-authenticating would not help.
        raise _STAFF_REQUIRED
    return principal.user
```

to:

```python
async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> User:
    """The authenticated staff/owner User. 403 for a customer principal.

    Signature is deliberately unchanged (`request` first, positional) — routes
    and the AUTH-04 tests call it directly.
    """
    principal = await resolve_principal(request, session)
    if principal.subject_type is not SubjectType.USER or principal.user is None:
        # A customer token is a valid credential for the wrong surface. 403,
        # not 401: re-authenticating would not help.
        raise _STAFF_REQUIRED

    # ADMIN-01 user/API monitors: best-effort request-volume counter, keyed
    # by tenant. Every authenticated staff/owner request funnels through
    # here, so this is the one place that instruments all of them without
    # touching 40+ individual route signatures. A Redis failure here must
    # never fail the request it's merely counting.
    if principal.user.tenant_id is not None:
        try:
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            await cache_service.incr(
                f"api_calls:{principal.user.tenant_id}:{day}", ttl=8 * 86400
            )
        except Exception:  # noqa: BLE001 — analytics counter, never fatal to the request
            logger.warning("admin.api_usage_counter_failed", tenant_id=str(principal.user.tenant_id))

    return principal.user
```

- [ ] **Step 4: Run to verify Step 1's tests pass**

Run: `pytest tests/unit/test_rbac.py -p no:langsmith_plugin -v`
Expected: PASS, every test in the file.

- [ ] **Step 5: Write the failing test for the 429 counter**

Create `tests/unit/test_rate_limiter.py`:

```python
"""Unit tests for the tracked rate-limit-exceeded handler (ADMIN-01 monitors)."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.principal import AuthProvider, Principal, SubjectType
from app.core.rate_limiter import rate_limit_exceeded_handler_with_tracking
from app.db.models.user import User


def make_request_with_principal(tenant_id, has_principal=True):
    request = MagicMock()
    request.state = MagicMock()
    if has_principal:
        user = User(tenant_id=tenant_id, role_id=uuid.uuid4(), is_active=True)
        user.id = uuid.uuid4()
        request.state.principal = Principal(
            subject_type=SubjectType.USER,
            tenant_id=tenant_id,
            auth_provider=AuthProvider.LOCAL,
            provider_subject=str(user.id),
            claims={},
            user=user,
        )
    else:
        del request.state.principal  # simulate no principal ever resolved
    return request


@pytest.mark.asyncio
async def test_tracks_429_when_principal_present(mocker):
    mocker.patch(
        "app.core.rate_limiter._rate_limit_exceeded_handler",
        MagicMock(return_value="the-response"),
    )
    incr = mocker.patch("app.core.rate_limiter.cache_service.incr", AsyncMock(return_value=1))
    tenant_id = uuid.uuid4()
    request = make_request_with_principal(tenant_id)

    response = await rate_limit_exceeded_handler_with_tracking(request, MagicMock())

    assert response == "the-response"
    incr.assert_awaited_once()
    assert incr.await_args.args[0].startswith(f"api_429:{tenant_id}:")


@pytest.mark.asyncio
async def test_skips_tracking_when_no_principal_resolved_yet(mocker):
    """A pre-auth rate limit (e.g. OTP request, login) has no tenant to
    attribute the 429 to — must not raise, just skip counting."""
    mocker.patch(
        "app.core.rate_limiter._rate_limit_exceeded_handler",
        MagicMock(return_value="the-response"),
    )
    incr = mocker.patch("app.core.rate_limiter.cache_service.incr", AsyncMock())
    request = make_request_with_principal(None, has_principal=False)

    response = await rate_limit_exceeded_handler_with_tracking(request, MagicMock())

    assert response == "the-response"
    incr.assert_not_awaited()
```

- [ ] **Step 6: Run to verify it fails**

Run: `pytest tests/unit/test_rate_limiter.py -p no:langsmith_plugin -v`
Expected: FAIL — `rate_limit_exceeded_handler_with_tracking` doesn't exist yet.

- [ ] **Step 7: Implement the tracked handler**

Replace the full contents of `app/core/rate_limiter.py`:

```python
"""QuickBite — SlowAPI rate limiting: IP + tenant + OTP-specific limits.

Every public endpoint needs a rate limit. One shared Limiter instance so
main.py registers a single exception handler; OTP endpoints add stricter
per-phone limits on top (NEW-OTP-01).

ADMIN-01: the registered handler also best-effort counts a 429 against the
caller's tenant (api_429:{tenant_id}:{yyyymmdd}, an 8-day rolling window,
Redis-only — same non-persisted convention as the api_calls counter in
api/v1/dependencies/auth.py). Attribution is only possible for a route that
authenticates before it rate-limits — a pre-auth 429 (login, OTP request)
has no tenant yet and is silently not counted, which is a known, accepted
gap: those flows are IP-rate-limited, not tenant-rate-limited, so a
per-tenant trip count wouldn't mean anything for them anyway.
"""

from datetime import datetime, timezone

import structlog
from fastapi import Request
from slowapi import Limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core import cache_service

logger = structlog.get_logger(__name__)

limiter = Limiter(key_func=get_remote_address)


async def rate_limit_exceeded_handler_with_tracking(request: Request, exc: RateLimitExceeded):
    principal = getattr(request.state, "principal", None)
    if principal is not None and principal.tenant_id is not None:
        try:
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            await cache_service.incr(f"api_429:{principal.tenant_id}:{day}", ttl=8 * 86400)
        except Exception:  # noqa: BLE001 — analytics counter, never fatal
            logger.warning("admin.api_429_counter_failed", tenant_id=str(principal.tenant_id))
    return _rate_limit_exceeded_handler(request, exc)
```

- [ ] **Step 8: Register the new handler in `main.py`**

Change:

```python
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1 import api_router
from app.api.v1.routers.pages import router as pages_router
from app.core.config import settings
from app.core.rate_limiter import limiter
```

to:

```python
from slowapi.errors import RateLimitExceeded

from app.api.v1 import api_router
from app.api.v1.routers.pages import router as pages_router
from app.core.config import settings
from app.core.rate_limiter import limiter, rate_limit_exceeded_handler_with_tracking
```

and:

```python
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

to:

```python
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler_with_tracking)
```

- [ ] **Step 9: Run to verify Step 5's tests pass**

Run: `pytest tests/unit/test_rate_limiter.py -p no:langsmith_plugin -v`
Expected: PASS.

- [ ] **Step 10: Write the failing test for the read endpoint**

Add to `tests/unit/test_admin_service.py`:

```python
# --- get_api_usage -------------------------------------------------------


@pytest.mark.asyncio
async def test_get_api_usage_reads_seven_days_of_both_counters(mocker):
    calls = mocker.patch(
        "app.services.admin_service.cache_service.get",
        AsyncMock(side_effect=lambda key: "42" if "api_calls" in key else "3"),
    )
    session = make_session([])

    response = await AdminService(session=session).get_api_usage(OTHER_TENANT_ID)

    assert len(response.days) == 7
    assert all(day.request_count == 42 for day in response.days)
    assert all(day.rate_limited_count == 3 for day in response.days)
    assert calls.await_count == 14  # 7 days x 2 counters


@pytest.mark.asyncio
async def test_get_api_usage_treats_missing_key_as_zero(mocker):
    mocker.patch("app.services.admin_service.cache_service.get", AsyncMock(return_value=None))
    session = make_session([])

    response = await AdminService(session=session).get_api_usage(OTHER_TENANT_ID)

    assert all(day.request_count == 0 and day.rate_limited_count == 0 for day in response.days)
```

- [ ] **Step 11: Run to verify it fails**

Run: `pytest tests/unit/test_admin_service.py -k get_api_usage -p no:langsmith_plugin -v`
Expected: FAIL.

- [ ] **Step 12: Implement**

Add `ApiUsageDay, ApiUsageResponse` to the schema import block in `admin_service.py` (`cache_service` and `datetime`/`timedelta`/`timezone` already imported from Task 5).

```python
    async def get_api_usage(self, tenant_id: uuid.UUID) -> ApiUsageResponse:
        today = datetime.now(timezone.utc).date()
        days: list[ApiUsageDay] = []
        for offset in range(7):
            day = today - timedelta(days=offset)
            day_str = day.strftime("%Y%m%d")
            calls_raw = await cache_service.get(f"api_calls:{tenant_id}:{day_str}")
            trips_raw = await cache_service.get(f"api_429:{tenant_id}:{day_str}")
            days.append(
                ApiUsageDay(
                    date=day.isoformat(),
                    request_count=int(calls_raw) if calls_raw is not None else 0,
                    rate_limited_count=int(trips_raw) if trips_raw is not None else 0,
                )
            )
        days.reverse()  # oldest first, matching the Sentiment Trend chart convention
        return ApiUsageResponse(tenant_id=tenant_id, days=days)
```

- [ ] **Step 13: Run to verify it passes**

Run: `pytest tests/unit/test_admin_service.py -p no:langsmith_plugin -v`
Expected: PASS, every test in the file.

- [ ] **Step 14: Add the route**

Extend `admin.py`'s schema import with `ApiUsageResponse`, add:

```python
@router.get(
    "/api-usage", response_model=ApiUsageResponse, status_code=status.HTTP_200_OK
)
async def get_api_usage(
    tenant_id: uuid.UUID,
    current_user: User = Depends(require_role(RoleLevel.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> ApiUsageResponse:
    return await AdminService(session=session).get_api_usage(tenant_id)
```

- [ ] **Step 15: Run the full unit suite**

Run: `pytest tests/unit/ -p no:langsmith_plugin -v --tb=short`
Expected: PASS, no regressions anywhere in the suite.

- [ ] **Step 16: Commit**

```bash
git add app/api/v1/dependencies/auth.py app/core/rate_limiter.py app/main.py \
        app/services/admin_service.py app/api/v1/routers/admin.py \
        tests/unit/test_rbac.py tests/unit/test_rate_limiter.py tests/unit/test_admin_service.py
git commit -m "feat(admin): track per-tenant API request volume and rate-limit trips

Request volume is counted once, centrally, inside get_current_user()
rather than a new dependency wired into every route — every
authenticated staff/owner request already funnels through it. 429
trips are counted in the existing SlowAPI exception handler, best-
effort in both cases so a Redis hiccup never fails the real request.
Redis-only, 8-day rolling window — this is an operational signal,
audit_logs already covers accountability."
```

---

### Task 8: Security flags

**Files:**
- Modify: `app/services/admin_service.py`, `app/api/v1/routers/admin.py`
- Test: `tests/unit/test_admin_service.py`

**Interfaces:**
- Consumes: `SecurityFlagsResponse` (Task 2), `User.failed_login_count`/`locked_until`, `customer.stamp_logs.is_fraudulent` (`app/db/models/loyalty.py`), `audit_logs` (existing).
- Produces: `AdminService.get_security_flags() -> SecurityFlagsResponse`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_admin_service.py`:

```python
from app.db.models.loyalty import StampLog


def make_stamp_log(**overrides) -> StampLog:
    defaults = {
        "tenant_id": OTHER_TENANT_ID,
        "branch_id": uuid.uuid4(),
        "gps_latitude_at_scan": 12.9,
        "gps_longitude_at_scan": 77.6,
        "distance_from_branch_m": 500.0,
        "is_fraudulent": True,
    }
    defaults.update(overrides)
    row = StampLog(**defaults)
    row.id = uuid.uuid4()
    row.scanned_at = datetime.now(timezone.utc)
    return row


# --- get_security_flags -------------------------------------------------------


@pytest.mark.asyncio
async def test_security_flags_aggregates_all_three_lists():
    locked_user = make_target_user(failed_login_count=5, locked_until=datetime(2027, 1, 1, tzinfo=timezone.utc))
    fraud_row = make_stamp_log()
    cluster_row = MagicMock(tenant_id=OTHER_TENANT_ID, count=4)
    session = make_session([[locked_user], [fraud_row], [cluster_row]])

    response = await AdminService(session=session).get_security_flags()

    assert len(response.locked_accounts) == 1
    assert response.locked_accounts[0].failed_login_count == 5
    assert len(response.fraud_flags) == 1
    assert response.fraud_flags[0].stamp_log_id == fraud_row.id
    assert len(response.force_logout_clusters) == 1
    assert response.force_logout_clusters[0].count == 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_admin_service.py -k security_flags -p no:langsmith_plugin -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add `from app.db.models.loyalty import StampLog` and `LockedAccountFlag, FraudFlag, ForceLogoutCluster, SecurityFlagsResponse` to the schema import block in `admin_service.py`.

```python
    async def get_security_flags(self) -> SecurityFlagsResponse:
        async with rls.admin_bypass_context(self.session):
            locked_result = await self.session.execute(
                select(User).where(
                    (User.failed_login_count > 0) | (User.locked_until.is_not(None))
                )
            )
            locked_users = locked_result.scalars().all()

            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            fraud_result = await self.session.execute(
                select(StampLog)
                .where(StampLog.is_fraudulent.is_(True), StampLog.scanned_at >= week_ago)
                .order_by(StampLog.scanned_at.desc())
                .limit(50)
            )
            fraud_rows = fraud_result.scalars().all()

            cluster_result = await self.session.execute(
                select(AuditLog.tenant_id, func.count().label("count"))
                .where(AuditLog.action == "admin.force_logout", AuditLog.created_at >= week_ago)
                .group_by(AuditLog.tenant_id)
                .having(func.count() >= 3)
            )
            clusters = cluster_result.all()

        return SecurityFlagsResponse(
            locked_accounts=[
                LockedAccountFlag(
                    user_id=u.id,
                    tenant_id=u.tenant_id,
                    failed_login_count=u.failed_login_count,
                    locked_until=u.locked_until,
                )
                for u in locked_users
            ],
            fraud_flags=[
                FraudFlag(
                    stamp_log_id=row.id,
                    tenant_id=row.tenant_id,
                    branch_id=row.branch_id,
                    scanned_at=row.scanned_at,
                )
                for row in fraud_rows
            ],
            force_logout_clusters=[
                ForceLogoutCluster(tenant_id=row.tenant_id, count=row.count) for row in clusters
            ],
        )
```

Note the test's third queued result is a list of row-like objects (`cluster_row`), matching `make_result`'s `result.scalars().all()`/`result.all()` duality — `make_result` in this file sets both `scalar_one_or_none` and `scalars().all()`; the grouped-count query above calls plain `.all()` on the result (not `.scalars().all()`, since it selects two columns, not one ORM entity), so confirm `make_result`'s behavior covers `result.all()` too before this test can pass — if it doesn't, extend `make_result` in this file:

```python
def make_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = value if isinstance(value, list) else []
    result.all.return_value = value if isinstance(value, list) else []
    return result
```

(adding the `result.all.return_value` line — check the file first; if this line already exists from an earlier task's needs, skip re-adding it.)

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_admin_service.py -p no:langsmith_plugin -v`
Expected: PASS, every test in the file.

- [ ] **Step 5: Add the route**

Extend `admin.py`'s schema import with `SecurityFlagsResponse`, add:

```python
@router.get(
    "/security-flags", response_model=SecurityFlagsResponse, status_code=status.HTTP_200_OK
)
async def get_security_flags(
    current_user: User = Depends(require_role(RoleLevel.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> SecurityFlagsResponse:
    return await AdminService(session=session).get_security_flags()
```

- [ ] **Step 6: Run the full unit suite one more time**

Run: `pytest tests/unit/ -p no:langsmith_plugin -v --tb=short`
Expected: PASS, no regressions. This confirms every backend task in this plan is now integrated correctly.

- [ ] **Step 7: Commit**

```bash
git add app/services/admin_service.py app/api/v1/routers/admin.py tests/unit/test_admin_service.py
git commit -m "feat(admin): add GET /admin/security-flags — locked accounts, fraud, force-logout clusters

Completes the four Super Admin backend capabilities (suspend/
reactivate, subscription override, health metrics, user/API monitors)."
```

---

## Part 2 — Frontend

All four pages below extend the `qb-glass` system documented in `DESIGN.md` and `static/css/dashboard.css` — no new CSS file, no new color, no new blur recipe. They copy the exact `<head>` contract and shell chrome from `app/templates/dashboard/index.html` (Tailwind CDN + inline config with the same Doc 4 brand-* tokens + `dashboard.css`), because that head block **is** the established system, not a placeholder to reinvent.

### Task 9: `admin-shell.js` — shared chrome, session gate, and API helper

**Files:**
- Create: `static/js/admin-shell.js`

**Interfaces:**
- Consumes: `quickbite_staff_session` in `sessionStorage` (existing, set at login — same object `dashboard-shell.js`/`billing.js`/`settings.js` already read).
- Produces: on `window`, nothing global — an IIFE, matching `dashboard-shell.js`'s own style. Every `admin-*.js` page script (Tasks 10-12) requires this script to run first (loaded before them in each template's `<script>` order) and relies on it having already redirected away any non-Super-Admin session before their own code runs.

- [ ] **Step 1: Write the file**

```javascript
/* QuickBite AI + Loyalty — Super Admin shell: session gate, account chip,
 * mobile tab bar wiring, and the shared apiFetch() every admin-*.js page
 * script uses. Modeled directly on dashboard-shell.js (same session key,
 * same account-sheet DOM contract) — the one difference is the redirect
 * target on failure: a non-Super-Admin session bounces to /dashboard
 * instead of /login, since they may well have a valid staff session, just
 * not this one's role.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';

  function readSession() {
    try {
      var raw = sessionStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (storageError) {
      return null;
    }
  }

  var session = readSession();
  if (!session || !session.access_token) {
    window.location.replace('/login');
    return;
  }
  if (session.role !== 'SUPER_ADMIN') {
    window.location.replace('/dashboard');
    return;
  }

  window.QuickBiteAdmin = {
    session: session,
    apiFetch: function (path, options) {
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
    },
  };

  // ---------- account chip (mirrors dashboard-shell.js exactly) ----------

  var initialsEls = document.querySelectorAll('[data-user-initials]');
  var roleLabelEl = document.querySelector('[data-account-sheet-role]');
  var label = 'Super Admin';
  initialsEls.forEach(function (el) { el.textContent = 'SA'; });
  if (roleLabelEl) roleLabelEl.textContent = label;

  var signOutBtn = document.querySelector('[data-account-signout]');
  if (signOutBtn) {
    signOutBtn.addEventListener('click', function () {
      sessionStorage.removeItem(SESSION_KEY);
      window.location.replace('/login');
    });
  }

  // ---------- mobile more/account sheet (same trigger contract as dashboard-shell.js) ----------

  var sheet = document.getElementById('more-sheet');
  var scrim = document.querySelector('[data-account-scrim]');
  var triggers = Array.prototype.slice.call(document.querySelectorAll('[data-account-trigger]'));

  function closeSheet() {
    if (sheet) sheet.hidden = true;
    if (scrim) scrim.hidden = true;
    triggers.forEach(function (t) { t.setAttribute('aria-expanded', 'false'); });
  }

  function openSheet(trigger) {
    if (!sheet) return;
    sheet.hidden = false;
    if (window.innerWidth < 900 && scrim) scrim.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
  }

  triggers.forEach(function (trigger) {
    trigger.addEventListener('click', function () {
      var isOpen = trigger.getAttribute('aria-expanded') === 'true';
      if (isOpen) { closeSheet(); } else { openSheet(trigger); }
    });
  });
  if (scrim) scrim.addEventListener('click', closeSheet);
})();
```

- [ ] **Step 2: No automated test — this file has no existing JS test harness (matches `dashboard-shell.js`/`billing.js`, neither of which has one). Verified manually in Task 13.**

- [ ] **Step 3: Commit**

```bash
git add static/js/admin-shell.js
git commit -m "feat(admin): add admin-shell.js — Super Admin session gate and shared chrome"
```

---

### Task 10: `admin/index.html` — tenants list, health KPI strip, suspend/reactivate, subscription override

**Files:**
- Create: `app/templates/admin/index.html`
- Create: `static/js/admin-tenants.js`

**Interfaces:**
- Consumes: `admin-shell.js`'s `window.QuickBiteAdmin.apiFetch`, `GET /admin/health-metrics`, `GET /admin/tenants`, `PATCH /admin/tenants/{id}/status`, `PATCH /admin/tenants/{id}/subscription` (all Part 1).
- Produces: nothing consumed by another task.

- [ ] **Step 1: Write the template**

Copy `app/templates/dashboard/index.html`'s lines 1-66 (the full `<head>` through `<body>` + `qb-dash-mesh` opening) **verbatim**, with two changes: `<title>Super Admin — QuickBite AI + Loyalty</title>`, and drop the `data-live-label`/`data-ws-indicator` WebSocket bits from the header (Super Admin has no live-stream endpoint) — everything else (Tailwind CDN script, the inline `tailwind.config` block with the exact same `brand-*`/`surface-*` tokens, `dashboard.css` link) is copied unchanged, because that head block is the system, not a per-page choice.

Then write a Super Admin-specific sidebar/tab-bar (3 destinations, not 8) and page body:

```html
<nav class="qb-glass qb-glass--sidebar fixed left-0 top-0 h-screen w-[240px] z-50 hidden min-[900px]:flex flex-col justify-between py-6">
  <div>
    <div class="px-6 mb-8">
      <h1 class="text-xl font-headline font-black text-primary-container flex items-center gap-2">
        <span class="material-symbols-outlined" style="font-variation-settings: 'FILL' 1;">shield_person</span>
        QuickBite
      </h1>
      <p class="text-xs text-brand-muted mt-1 font-medium">Super Admin</p>
    </div>
    <div class="flex flex-col gap-1 font-body text-sm">
      <a class="flex items-center gap-3 bg-primary-container text-on-primary-container font-bold rounded-lg px-4 py-3 mx-2" href="/admin" aria-current="page">
        <span class="material-symbols-outlined" style="font-variation-settings: 'FILL' 1;">apartment</span>
        Tenants
      </a>
      <a class="flex items-center gap-3 text-on-surface-variant hover:text-primary-container px-4 py-3 mx-2 hover:bg-white/50 transition-colors duration-200 rounded-lg group" href="/admin/audit-logs">
        <span class="material-symbols-outlined text-brand-muted group-hover:text-primary-container transition-colors">receipt_long</span>
        Audit Logs
      </a>
      <a class="flex items-center gap-3 text-on-surface-variant hover:text-primary-container px-4 py-3 mx-2 hover:bg-white/50 transition-colors duration-200 rounded-lg group" href="/admin/monitors">
        <span class="material-symbols-outlined text-brand-muted group-hover:text-primary-container transition-colors">monitor_heart</span>
        Monitors
      </a>
    </div>
  </div>
  <div class="px-4">
    <button type="button" class="qb-account-trigger pt-4 border-t border-outline-variant/60 flex items-center gap-3 px-2 py-2"
            data-account-trigger aria-haspopup="true" aria-expanded="false" aria-controls="more-sheet">
      <div class="w-10 h-10 rounded-full bg-brand-primary/10 text-brand-primary flex items-center justify-center font-bold text-sm" data-user-initials aria-hidden="true">SA</div>
      <p class="text-sm font-bold text-brand-ink" data-account-sheet-role>Super Admin</p>
    </button>
  </div>
</nav>

<nav class="qb-glass qb-mobile-tabbar min-[900px]:hidden" aria-label="Super Admin">
  <a class="qb-tab" href="/admin" aria-current="page">
    <span class="material-symbols-outlined" style="font-variation-settings: 'FILL' 1;" aria-hidden="true">apartment</span>
    Tenants
  </a>
  <a class="qb-tab" href="/admin/monitors">
    <span class="material-symbols-outlined" aria-hidden="true">monitor_heart</span>
    Monitors
  </a>
  <button type="button" class="qb-tab" data-account-trigger aria-haspopup="true" aria-expanded="false" aria-controls="more-sheet">
    <span class="material-symbols-outlined" aria-hidden="true">more_horiz</span>
    More
  </button>
</nav>

<div class="qb-account-scrim min-[900px]:hidden" data-account-scrim hidden></div>
<div class="qb-glass qb-account-sheet" id="more-sheet" role="dialog" aria-label="Account" hidden>
  <div class="min-[900px]:hidden">
    <a class="qb-more-nav-item" href="/admin/audit-logs">
      <span class="material-symbols-outlined" aria-hidden="true">receipt_long</span>
      Audit Logs
    </a>
    <div class="qb-more-divider"></div>
  </div>
  <div class="qb-account-sheet-head">
    <div class="w-10 h-10 rounded-full bg-brand-primary/10 text-brand-primary flex items-center justify-center font-bold text-sm" aria-hidden="true">SA</div>
    <p class="text-sm font-bold text-brand-ink">Super Admin</p>
  </div>
  <button type="button" class="qb-account-signout" data-account-signout>
    <span class="material-symbols-outlined" aria-hidden="true">logout</span>
    Sign out
  </button>
</div>

<main class="min-[900px]:ml-[240px] flex-1 min-h-screen">
  <header class="qb-glass sticky top-0 right-0 z-40 border-x-0 border-t-0 rounded-none flex justify-between items-center w-full px-5 min-[900px]:px-8 h-16">
    <h2 class="font-body font-bold text-brand-ink tracking-tight text-xl">Tenants</h2>
  </header>

  <div class="p-5 min-[900px]:p-8 pb-28 min-[900px]:pb-8 max-w-7xl mx-auto space-y-6">
    <div id="admin-error" class="hidden bg-brand-danger/10 text-brand-danger text-sm font-medium px-4 py-3 rounded-lg" role="alert"></div>

    <div class="qb-glass qb-kpi-strip" style="grid-template-columns: repeat(3, 1fr);">
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

    <div class="qb-glass overflow-hidden">
      <div class="p-5 border-b border-outline-variant/20">
        <input type="search" class="qb-invite-input w-full max-w-sm" placeholder="Search by subdomain or name…" data-tenant-search />
      </div>
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs font-bold uppercase tracking-wide text-brand-muted border-b border-outline-variant/20">
            <th class="px-5 py-3">Tenant</th>
            <th class="px-5 py-3">Subdomain</th>
            <th class="px-5 py-3">Status</th>
            <th class="px-5 py-3">Actions</th>
          </tr>
        </thead>
        <tbody data-tenant-rows></tbody>
      </table>
    </div>

    <template id="tenant-row-template">
      <tr class="border-b border-outline-variant/10">
        <td class="px-5 py-3 font-semibold text-brand-ink" data-cell="name"></td>
        <td class="px-5 py-3 text-brand-muted" data-cell="subdomain"></td>
        <td class="px-5 py-3">
          <span class="text-xs font-bold px-2.5 py-1 rounded-full" data-cell="status-badge"></span>
        </td>
        <td class="px-5 py-3 flex flex-wrap gap-2">
          <button type="button" class="qb-plan-cta" data-action="toggle-status"></button>
          <button type="button" class="qb-program-action" data-action="toggle-override">Override plan</button>
        </td>
      </tr>
      <tr class="border-b border-outline-variant/10" data-override-row hidden>
        <td colspan="4" class="px-5 py-4">
          <form class="qb-glass p-4 flex flex-wrap gap-3 items-end" data-override-form>
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
            <div class="qb-invite-field flex-1 min-w-[240px]">
              <label class="qb-invite-label">Reason (required)</label>
              <input class="qb-invite-input w-full" name="reason" required minlength="3" maxlength="500" />
            </div>
            <button type="submit" class="qb-invite-submit">Save override</button>
          </form>
        </td>
      </tr>
    </template>
  </div>
</main>

<script src="{{ url_for('static', path='js/admin-shell.js') }}" defer></script>
<script src="{{ url_for('static', path='js/admin-tenants.js') }}" defer></script>
</body>
</html>
```

- [ ] **Step 2: Write `static/js/admin-tenants.js`**

```javascript
/* QuickBite AI + Loyalty — Super Admin tenants page: health KPI strip,
 * tenant table, suspend/reactivate, and the inline (not modal — matches
 * settings.html's invite-panel convention) subscription-override form.
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

    var rowsMount = document.querySelector('[data-tenant-rows]');
    var template = document.getElementById('tenant-row-template');
    var allTenants = [];

    function renderRow(tenant) {
      var rowFrag = template.content.cloneNode(true);
      var mainRow = rowFrag.querySelector('tr:first-child');
      var overrideRow = rowFrag.querySelector('[data-override-row]');

      mainRow.querySelector('[data-cell="name"]').textContent = tenant.name;
      mainRow.querySelector('[data-cell="subdomain"]').textContent = tenant.subdomain;

      var badge = mainRow.querySelector('[data-cell="status-badge"]');
      badge.textContent = tenant.is_active ? 'Active' : 'Suspended';
      badge.className = 'text-xs font-bold px-2.5 py-1 rounded-full ' +
        (tenant.is_active ? 'bg-brand-success/10 text-brand-success' : 'bg-brand-danger/10 text-brand-danger');

      var toggleBtn = mainRow.querySelector('[data-action="toggle-status"]');
      toggleBtn.textContent = tenant.is_active ? 'Suspend' : 'Reactivate';
      toggleBtn.addEventListener('click', function () {
        var nextState = !tenant.is_active;
        var verb = nextState ? 'reactivate' : 'suspend';
        if (!window.confirm('Are you sure you want to ' + verb + ' ' + tenant.name + '?')) return;
        admin.apiFetch('/admin/tenants/' + tenant.tenant_id + '/status', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ is_active: nextState }),
        }).then(function () {
          tenant.is_active = nextState;
          loadTenants();
          loadMetrics();
        }).catch(function (error) {
          if (error.message !== 'unauthorized') showError(error.message);
        });
      });

      var overrideToggle = mainRow.querySelector('[data-action="toggle-override"]');
      overrideToggle.addEventListener('click', function () {
        overrideRow.hidden = !overrideRow.hidden;
      });

      var overrideForm = overrideRow.querySelector('[data-override-form]');
      overrideForm.addEventListener('submit', function (event) {
        event.preventDefault();
        var formData = new FormData(overrideForm);
        var body = { reason: formData.get('reason') };
        var status = formData.get('status');
        if (status) body.status = status;
        admin.apiFetch('/admin/tenants/' + tenant.tenant_id + '/subscription', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }).then(function () {
          overrideRow.hidden = true;
          overrideForm.reset();
        }).catch(function (error) {
          if (error.message !== 'unauthorized') showError(error.message);
        });
      });

      return rowFrag;
    }

    function renderTenants(list) {
      rowsMount.innerHTML = '';
      list.forEach(function (tenant) {
        rowsMount.appendChild(renderRow(tenant));
      });
    }

    function loadTenants() {
      return admin.apiFetch('/admin/tenants').then(function (data) {
        allTenants = data.tenants;
        renderTenants(allTenants);
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    var searchInput = document.querySelector('[data-tenant-search]');
    if (searchInput) {
      searchInput.addEventListener('input', function () {
        var term = searchInput.value.trim().toLowerCase();
        var filtered = allTenants.filter(function (t) {
          return t.name.toLowerCase().indexOf(term) !== -1 ||
            t.subdomain.toLowerCase().indexOf(term) !== -1;
        });
        renderTenants(filtered);
      });
    }

    loadMetrics();
    loadTenants();
  });
})();
```

- [ ] **Step 3: Commit**

```bash
git add app/templates/admin/index.html static/js/admin-tenants.js
git commit -m "feat(admin): add Super Admin tenants page — list, health KPIs, suspend/reactivate, plan override"
```

---

### Task 11: `admin/audit_logs.html` — filterable audit log viewer

**Files:**
- Create: `app/templates/admin/audit_logs.html`
- Create: `static/js/admin-audit-logs.js`

**Interfaces:**
- Consumes: `admin-shell.js`, `GET /admin/audit-logs` (existing, `AuditLogFilters` schema already supports `tenant_id`/`action`/`date_from`/`date_to`/`limit`/`offset`).

- [ ] **Step 1: Write the template**

Same head/shell/nav structure as Task 10's `admin/index.html` (copy that file's `<head>` through the mobile sheet verbatim, change `<title>` to `Audit Logs — Super Admin` and mark `href="/admin/audit-logs"` as the `aria-current="page"` sidebar/tab item instead of Tenants), then the page body:

```html
<main class="min-[900px]:ml-[240px] flex-1 min-h-screen">
  <header class="qb-glass sticky top-0 right-0 z-40 border-x-0 border-t-0 rounded-none flex justify-between items-center w-full px-5 min-[900px]:px-8 h-16">
    <h2 class="font-body font-bold text-brand-ink tracking-tight text-xl">Audit Logs</h2>
  </header>

  <div class="p-5 min-[900px]:p-8 pb-28 min-[900px]:pb-8 max-w-7xl mx-auto space-y-6">
    <div id="admin-error" class="hidden bg-brand-danger/10 text-brand-danger text-sm font-medium px-4 py-3 rounded-lg" role="alert"></div>

    <form class="qb-glass p-4 flex flex-wrap gap-3 items-end" data-filter-form>
      <div class="qb-invite-field">
        <label class="qb-invite-label">Action</label>
        <input class="qb-invite-input" name="action" placeholder="admin.force_logout" />
      </div>
      <div class="qb-invite-field">
        <label class="qb-invite-label">Tenant ID</label>
        <input class="qb-invite-input" name="tenant_id" placeholder="uuid" />
      </div>
      <div class="qb-invite-field">
        <label class="qb-invite-label">From</label>
        <input class="qb-invite-input" name="date_from" type="date" />
      </div>
      <div class="qb-invite-field">
        <label class="qb-invite-label">To</label>
        <input class="qb-invite-input" name="date_to" type="date" />
      </div>
      <button type="submit" class="qb-invite-submit">Filter</button>
    </form>

    <div class="qb-glass overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs font-bold uppercase tracking-wide text-brand-muted border-b border-outline-variant/20">
            <th class="px-5 py-3">When</th>
            <th class="px-5 py-3">Action</th>
            <th class="px-5 py-3">Resource</th>
            <th class="px-5 py-3">Tenant</th>
          </tr>
        </thead>
        <tbody data-log-rows></tbody>
      </table>
      <div class="qb-empty hidden" data-log-empty>
        <span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">receipt_long</span>
        <p class="text-sm font-semibold text-brand-ink">No matching audit log entries</p>
      </div>
    </div>
  </div>
</main>

<script src="{{ url_for('static', path='js/admin-shell.js') }}" defer></script>
<script src="{{ url_for('static', path='js/admin-audit-logs.js') }}" defer></script>
</body>
</html>
```

- [ ] **Step 2: Write `static/js/admin-audit-logs.js`**

```javascript
/* QuickBite AI + Loyalty — Super Admin audit log viewer. */
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

    var rowsMount = document.querySelector('[data-log-rows]');
    var emptyState = document.querySelector('[data-log-empty]');

    function renderEntries(entries) {
      rowsMount.innerHTML = '';
      emptyState.classList.toggle('hidden', entries.length > 0);
      entries.forEach(function (entry) {
        var tr = document.createElement('tr');
        tr.className = 'border-b border-outline-variant/10';
        tr.innerHTML =
          '<td class="px-5 py-3 text-brand-muted">' + new Date(entry.created_at).toLocaleString() + '</td>' +
          '<td class="px-5 py-3 font-mono text-xs text-brand-ink">' + entry.action + '</td>' +
          '<td class="px-5 py-3 text-brand-muted">' + (entry.resource_type || '—') + '</td>' +
          '<td class="px-5 py-3 text-brand-muted font-mono text-xs">' + (entry.tenant_id || '—') + '</td>';
        rowsMount.appendChild(tr);
      });
    }

    function loadLogs(params) {
      var query = new URLSearchParams();
      Object.keys(params || {}).forEach(function (key) {
        if (params[key]) query.set(key, params[key]);
      });
      var qs = query.toString();
      return admin.apiFetch('/admin/audit-logs' + (qs ? '?' + qs : '')).then(function (data) {
        renderEntries(data.entries);
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    var form = document.querySelector('[data-filter-form]');
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var formData = new FormData(form);
      loadLogs({
        action: formData.get('action'),
        tenant_id: formData.get('tenant_id'),
        date_from: formData.get('date_from'),
        date_to: formData.get('date_to'),
      });
    });

    loadLogs({});
  });
})();
```

- [ ] **Step 3: Commit**

```bash
git add app/templates/admin/audit_logs.html static/js/admin-audit-logs.js
git commit -m "feat(admin): add Super Admin audit log viewer page"
```

---

### Task 12: `admin/monitors.html` — sessions, API usage, security flags

**Files:**
- Create: `app/templates/admin/monitors.html`
- Create: `static/js/admin-monitors.js`

**Interfaces:**
- Consumes: `admin-shell.js`, `GET /admin/sessions`, `GET /admin/api-usage?tenant_id=`, `GET /admin/security-flags`, `POST /admin/users/{user_id}/force-logout` (existing).

- [ ] **Step 1: Write the template**

Same head/shell/nav as Task 10/11 (copy verbatim, `<title>Monitors — Super Admin</title>`, `href="/admin/monitors"` as the current nav item), page body:

```html
<main class="min-[900px]:ml-[240px] flex-1 min-h-screen">
  <header class="qb-glass sticky top-0 right-0 z-40 border-x-0 border-t-0 rounded-none flex justify-between items-center w-full px-5 min-[900px]:px-8 h-16">
    <h2 class="font-body font-bold text-brand-ink tracking-tight text-xl">Monitors</h2>
  </header>

  <div class="p-5 min-[900px]:p-8 pb-28 min-[900px]:pb-8 max-w-7xl mx-auto space-y-6">
    <div id="admin-error" class="hidden bg-brand-danger/10 text-brand-danger text-sm font-medium px-4 py-3 rounded-lg" role="alert"></div>

    <!-- Security flags — three glass panels, not a uniform icon-card grid -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="qb-glass p-5">
        <h3 class="text-sm font-bold text-brand-ink mb-3">Locked Accounts</h3>
        <ul class="space-y-2 text-xs text-brand-muted" data-locked-list></ul>
      </div>
      <div class="qb-glass p-5">
        <h3 class="text-sm font-bold text-brand-ink mb-3">Fraud Flags (7d)</h3>
        <ul class="space-y-2 text-xs text-brand-muted" data-fraud-list></ul>
      </div>
      <div class="qb-glass p-5">
        <h3 class="text-sm font-bold text-brand-ink mb-3">Force-Logout Clusters (7d)</h3>
        <ul class="space-y-2 text-xs text-brand-muted" data-cluster-list></ul>
      </div>
    </div>

    <!-- API usage lookup -->
    <div class="qb-glass p-5">
      <h3 class="text-sm font-bold text-brand-ink mb-3">API Usage (7-day, by tenant)</h3>
      <form class="flex gap-3 mb-4" data-usage-form>
        <input class="qb-invite-input flex-1" name="tenant_id" placeholder="Tenant ID (uuid)" required />
        <button type="submit" class="qb-invite-submit">Look up</button>
      </form>
      <div id="usage-chart" class="min-h-[160px]"></div>
    </div>

    <!-- Active sessions -->
    <div class="qb-glass overflow-hidden">
      <div class="p-5 border-b border-outline-variant/20 flex items-center justify-between">
        <h3 class="text-sm font-bold text-brand-ink">Active Sessions</h3>
        <input class="qb-invite-input" data-session-user-filter placeholder="Filter by user ID" />
      </div>
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs font-bold uppercase tracking-wide text-brand-muted border-b border-outline-variant/20">
            <th class="px-5 py-3">User</th>
            <th class="px-5 py-3">Tenant</th>
            <th class="px-5 py-3">User Agent</th>
            <th class="px-5 py-3">Expires</th>
            <th class="px-5 py-3">Actions</th>
          </tr>
        </thead>
        <tbody data-session-rows></tbody>
      </table>
    </div>
  </div>
</main>

<script src="{{ url_for('static', path='js/admin-shell.js') }}" defer></script>
<script src="{{ url_for('static', path='js/admin-monitors.js') }}" defer></script>
</body>
</html>
```

- [ ] **Step 2: Write `static/js/admin-monitors.js`**

```javascript
/* QuickBite AI + Loyalty — Super Admin monitors: security flags, per-tenant
 * API usage lookup, active sessions with inline force-logout. Chart is a
 * hand-rolled SVG bar strip — no charting dependency, matching the
 * Sentiment Trend convention in DESIGN.md.
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

    function loadSecurityFlags() {
      admin.apiFetch('/admin/security-flags').then(function (data) {
        var lockedList = document.querySelector('[data-locked-list]');
        lockedList.innerHTML = data.locked_accounts.length
          ? data.locked_accounts.map(function (row) {
              return '<li>' + row.user_id + ' — ' + row.failed_login_count + ' failed attempts</li>';
            }).join('')
          : '<li>No locked accounts.</li>';

        var fraudList = document.querySelector('[data-fraud-list]');
        fraudList.innerHTML = data.fraud_flags.length
          ? data.fraud_flags.map(function (row) {
              return '<li>' + new Date(row.scanned_at).toLocaleDateString() + ' — tenant ' + (row.tenant_id || '—') + '</li>';
            }).join('')
          : '<li>No fraud flags this week.</li>';

        var clusterList = document.querySelector('[data-cluster-list]');
        clusterList.innerHTML = data.force_logout_clusters.length
          ? data.force_logout_clusters.map(function (row) {
              return '<li>Tenant ' + (row.tenant_id || '—') + ' — ' + row.count + ' force-logouts</li>';
            }).join('')
          : '<li>No clusters this week.</li>';
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    function renderUsageChart(days) {
      var mount = document.getElementById('usage-chart');
      var max = Math.max.apply(null, days.map(function (d) { return d.request_count; }).concat([1]));
      var barWidth = 100 / days.length;
      var bars = days.map(function (d, i) {
        var heightPct = (d.request_count / max) * 100;
        return '<rect x="' + (i * barWidth) + '%" y="' + (100 - heightPct) + '%" width="' + (barWidth - 2) + '%" height="' + heightPct + '%" fill="#1A56DB" rx="2"></rect>';
      }).join('');
      mount.innerHTML = '<svg viewBox="0 0 100 100" preserveAspectRatio="none" style="width:100%;height:160px;">' + bars + '</svg>';
    }

    var usageForm = document.querySelector('[data-usage-form]');
    usageForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var tenantId = new FormData(usageForm).get('tenant_id');
      admin.apiFetch('/admin/api-usage?tenant_id=' + encodeURIComponent(tenantId)).then(function (data) {
        renderUsageChart(data.days);
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    });

    var sessionRows = document.querySelector('[data-session-rows]');

    function renderSessions(sessions) {
      sessionRows.innerHTML = sessions.map(function (s) {
        return '<tr class="border-b border-outline-variant/10">' +
          '<td class="px-5 py-3 font-mono text-xs">' + s.user_id + '</td>' +
          '<td class="px-5 py-3 font-mono text-xs">' + (s.tenant_id || '—') + '</td>' +
          '<td class="px-5 py-3 text-brand-muted text-xs">' + s.user_agent + '</td>' +
          '<td class="px-5 py-3 text-brand-muted text-xs">' + new Date(s.expires_at).toLocaleString() + '</td>' +
          '<td class="px-5 py-3">' +
          (s.revoked
            ? '<span class="text-xs text-brand-muted">Revoked</span>'
            : '<button type="button" class="qb-program-action" data-force-logout="' + s.user_id + '">Force logout</button>') +
          '</td></tr>';
      }).join('');
    }

    function loadSessions(userId) {
      var qs = userId ? '?user_id=' + encodeURIComponent(userId) : '';
      admin.apiFetch('/admin/sessions' + qs).then(function (data) {
        renderSessions(data.sessions);
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    document.addEventListener('click', function (event) {
      var btn = event.target.closest('[data-force-logout]');
      if (!btn) return;
      var userId = btn.dataset.forceLogout;
      if (!window.confirm('Force logout this user from every device?')) return;
      admin.apiFetch('/admin/users/' + userId + '/force-logout', { method: 'POST' }).then(function () {
        loadSessions(document.querySelector('[data-session-user-filter]').value.trim());
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    });

    var userFilter = document.querySelector('[data-session-user-filter]');
    var debounceHandle;
    userFilter.addEventListener('input', function () {
      window.clearTimeout(debounceHandle);
      debounceHandle = window.setTimeout(function () {
        loadSessions(userFilter.value.trim());
      }, 300);
    });

    loadSecurityFlags();
    loadSessions('');
  });
})();
```

- [ ] **Step 3: Commit**

```bash
git add app/templates/admin/monitors.html static/js/admin-monitors.js
git commit -m "feat(admin): add Super Admin monitors page — security flags, API usage, active sessions with force-logout"
```

---

### Task 13: Wire the three page routes and verify manually

**Files:**
- Modify: `app/api/v1/routers/pages.py`

**Interfaces:**
- Consumes: nothing new — plain `Jinja2Templates.TemplateResponse`, same pattern every existing page route in this file already uses.

- [ ] **Step 1: Add the three routes**

Append to `app/api/v1/routers/pages.py` (after the existing `/dashboard/billing` route):

```python
@router.get("/admin", response_class=HTMLResponse)
async def admin_tenants_page(request: Request) -> HTMLResponse:
    """Super Admin tenants shell (ADMIN-01). Same no-server-side-auth-check
    convention as every /dashboard/* page: admin-shell.js reads GET
    /auth/me-equivalent (the sessionStorage role) and redirects to
    /dashboard if the caller isn't SUPER_ADMIN. Every real mutation is
    still enforced by require_role(SUPER_ADMIN) server-side regardless."""
    return templates.TemplateResponse(request, "admin/index.html")


@router.get("/admin/audit-logs", response_class=HTMLResponse)
async def admin_audit_logs_page(request: Request) -> HTMLResponse:
    """Same shell convention as admin_tenants_page above."""
    return templates.TemplateResponse(request, "admin/audit_logs.html")


@router.get("/admin/monitors", response_class=HTMLResponse)
async def admin_monitors_page(request: Request) -> HTMLResponse:
    """Same shell convention as admin_tenants_page above."""
    return templates.TemplateResponse(request, "admin/monitors.html")
```

- [ ] **Step 2: Confirm the app starts and every backend test still passes**

Run: `pytest tests/unit/ -p no:langsmith_plugin -v --tb=short`
Expected: PASS, no regressions.

Run: start the dev server per this repo's normal run configuration (`docker-compose up -d` then the app, per AGENTS.md's documented commands).

- [ ] **Step 3: Playwright verification — desktop, as Super Admin**

Using the `mcp__playwright__*` tools directly (or the `/qa` skill, which wraps them): `browser_navigate` to the login page and sign in as a seeded Super Admin account (or seed one first via `python scripts/seed_roles.py` per AGENTS.md's documented seed commands if none exists in the dev DB), then `browser_navigate` to `/admin`. Use `browser_snapshot` to confirm the KPI strip shows non-skeleton numbers and the tenant table has rows. `browser_click` a "Suspend" button, confirm via `browser_snapshot` the badge flips to "Suspended" and the KPI strip's suspended count increments. `browser_click` "Override plan", `browser_fill_form` the reason field, submit, confirm the row collapses back with no error banner. `browser_navigate` to `/admin/audit-logs`, fill the action filter, submit, `browser_snapshot` to confirm the table narrows. `browser_navigate` to `/admin/monitors`, confirm the three flag panels and the sessions table render via `browser_snapshot`, submit the API-usage lookup form with a real tenant ID, confirm the SVG bar chart appears, `browser_click` a "Force logout" button and confirm the row updates.

- [ ] **Step 4: Playwright verification — mobile viewport (<900px), as Super Admin**

`browser_resize` to a width below 900px (e.g. 390x844, an iPhone-class viewport) on all three admin pages. Use `browser_snapshot` to confirm the fixed sidebar is absent and the `qb-mobile-tabbar` is present at the bottom with working tabs (`browser_click` each tab, confirm navigation), and that opening the account/More sheet shows a bottom sheet with a dimming scrim rather than the desktop popover — matching the existing Owner dashboard's already-verified mobile behavior (`.impeccable/review/` in this repo has prior mobile screenshots of that pattern to compare against).

- [ ] **Step 5: Playwright verification — as Owner/Manager/Staff (negative case)**

Log out, sign back in as an Owner (or Manager/Staff) account, `browser_navigate` to `/admin` directly, and use `browser_snapshot` immediately after navigation to confirm the page redirects to `/dashboard` before any tenant data renders (no flash of real tenant names in the snapshot). Then use `browser_network_request` (or a direct `curl`/Postman call outside Playwright) to `GET /api/v1/admin/tenants` with that account's bearer token and confirm the response is `403 INSUFFICIENT_PERMISSIONS`.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/routers/pages.py
git commit -m "feat(admin): wire /admin, /admin/audit-logs, /admin/monitors page routes

Completes the Super Admin dashboard — backend (Part 1, Tasks 1-8) and
frontend (Part 2, Tasks 9-13) both land in this plan."
```
