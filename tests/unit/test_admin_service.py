"""Unit tests for AdminService — Super Admin panel (ADMIN-01).

One test per acceptance criterion:
- GET /admin/tenants: role-gating is require_role's job (tested in
  test_rbac.py) — here we only cover AdminService.list_tenants itself, and
  that it returns every tenant regardless of the caller's own tenant.
- force-logout invalidates all target sessions (AuthService.logout_all,
  already unit-tested elsewhere, is asserted as *called* here).
- audit-logs are filterable by tenant_id / action / date range.
- every admin action writes its own audit_log entry.

DB session is mocked per house convention (see test_team.py's make_session).
rls.admin_bypass_context is mocked as a no-op so the execute-result list only
needs to account for the service's own queries — same rationale as
test_identity_link_service.py mocking rls.set_tenant_context/clear_tenant_context.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.db.models.audit import AuditLog
from app.db.models.loyalty import StampLog
from app.db.models.subscription import Subscription
from app.db.models.tenant import Tenant
from app.db.models.user import Session as UserSession
from app.db.models.user import User
from app.schemas.admin import AuditLogFilters, SubscriptionOverrideRequest
from app.services.admin_service import AdminService

ADMIN_TENANT_ID = uuid.uuid4()
OTHER_TENANT_ID = uuid.uuid4()


def make_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = value if isinstance(value, list) else []
    result.all.return_value = value if isinstance(value, list) else []
    return result


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock(side_effect=[make_result(v) for v in execute_results])
    return session


def added(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


def make_admin(**overrides) -> User:
    defaults = {"tenant_id": ADMIN_TENANT_ID, "role_id": uuid.uuid4(), "is_active": True}
    defaults.update(overrides)
    admin = User(**defaults)
    admin.id = uuid.uuid4()
    return admin


def make_target_user(**overrides) -> User:
    defaults = {"tenant_id": OTHER_TENANT_ID, "role_id": uuid.uuid4(), "is_active": True}
    defaults.update(overrides)
    user = User(**defaults)
    user.id = uuid.uuid4()
    return user


def make_tenant(**overrides) -> Tenant:
    defaults = {
        "subdomain": "marcos",
        "name": "Marco's",
        "onboarding_state": "active",
        "is_active": True,
    }
    defaults.update(overrides)
    tenant = Tenant(**defaults)
    tenant.id = uuid.uuid4()
    tenant.created_at = datetime.now(timezone.utc)
    return tenant


def make_audit_log(**overrides) -> AuditLog:
    defaults = {
        "tenant_id": OTHER_TENANT_ID,
        "user_id": uuid.uuid4(),
        "action": "login_success",
        "resource_type": None,
        "resource_id": None,
        "event_metadata": None,
    }
    defaults.update(overrides)
    entry = AuditLog(**defaults)
    entry.id = uuid.uuid4()
    entry.created_at = datetime.now(timezone.utc)
    return entry


@asynccontextmanager
async def _noop_admin_bypass(session):  # noqa: ARG001
    yield


@pytest.fixture(autouse=True)
def _mock_admin_bypass(mocker):
    mocker.patch("app.services.admin_service.rls.admin_bypass_context", _noop_admin_bypass)


def make_add_tracking_bypass(session: MagicMock):
    """A drop-in replacement for `rls.admin_bypass_context` that records
    whether `session.add()` was called while the context was still open.

    `quickbite_admin_bypass` only has SELECT granted on
    `restaurant.audit_logs` (migration 0010) — an `AuditLog` INSERT issued
    while still inside `admin_bypass_context` would run under that role in
    production and fail with `InsufficientPrivilegeError`. The regular
    `_noop_admin_bypass` fixture can't see this ordering bug at all (it's a
    pure no-op), so this variant exists specifically to prove the AuditLog
    add happens *after* the bypass block has exited.
    """
    state = {"active": False, "add_called_while_active": False}
    original_add = session.add

    def tracking_add(*args, **kwargs):
        if state["active"]:
            state["add_called_while_active"] = True
        return original_add(*args, **kwargs)

    session.add = MagicMock(side_effect=tracking_add)

    @asynccontextmanager
    async def bypass(_session):  # noqa: ARG001
        state["active"] = True
        try:
            yield
        finally:
            state["active"] = False

    return bypass, state


# --- list_tenants ---------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tenants_returns_every_tenant_regardless_of_caller():
    """`restaurant.tenants` is RLS-exempt (0009) — every tenant must come back,
    not just the caller's own."""
    mine = make_tenant(name="Marco's")
    other = make_tenant(name="Rival's Diner")
    session = make_session([
        [mine, other],  # 1. base tenant list
        [],             # 2. subscription+plan join (no subscriptions)
        [],             # 3. branch counts (no branches)
        [],             # 4. staff counts (no staff)
        [],             # 5. last-active (no activity)
    ])

    response = await AdminService(session=session).list_tenants()

    assert {t.name for t in response.tenants} == {"Marco's", "Rival's Diner"}


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


@pytest.mark.asyncio
async def test_list_tenants_branch_and_staff_counts_filter_soft_deleted_rows():
    """Branch.is_active and User.is_active are soft-delete flags (a closed
    branch or a removed team member keeps its row rather than being deleted)
    — both aggregate queries must filter on is_active=True or they overcount."""
    tenant = make_tenant(name="Marco's")
    session = make_session([
        [tenant],  # 1. base tenant list
        [],        # 2. subscription+plan join
        [],        # 3. branch counts
        [],        # 4. staff counts
        [],        # 5. last-active
    ])

    await AdminService(session=session).list_tenants()

    branch_query = session.execute.await_args_list[2].args[0]
    assert "branches.is_active" in str(branch_query.whereclause)

    staff_query = session.execute.await_args_list[3].args[0]
    assert "users.is_active" in str(staff_query.whereclause)


@pytest.mark.asyncio
async def test_list_tenants_staff_count_excludes_super_admin_role():
    """Every role except USER always carries a tenant_id (User's own
    docstring) — a SUPER_ADMIN account's tenant_id is not the tenant they
    administer, so the staff_count query must join Role and exclude it."""
    tenant = make_tenant(name="Marco's")
    session = make_session([
        [tenant],  # 1. base tenant list
        [],        # 2. subscription+plan join
        [],        # 3. branch counts
        [],        # 4. staff counts
        [],        # 5. last-active
    ])

    await AdminService(session=session).list_tenants()

    staff_query = session.execute.await_args_list[3].args[0]
    compiled_where = str(
        staff_query.whereclause.compile(compile_kwargs={"literal_binds": True})
    )
    assert "roles.name" in compiled_where
    assert "SUPER_ADMIN" in compiled_where


# --- force_logout_user ------------------------------------------------------


@pytest.mark.asyncio
async def test_force_logout_invalidates_target_sessions_and_audit_logs(mocker):
    admin = make_admin()
    target = make_target_user()
    logout_all = mocker.patch(
        "app.services.admin_service.AuthService.logout_all", AsyncMock()
    )
    session = make_session([target])

    response = await AdminService(session=session).force_logout_user(target.id, admin)

    assert response.status == "logged_out"
    assert response.user_id == target.id
    logout_all.assert_awaited_once_with(target.id)
    entry = added(session, AuditLog)[-1]
    assert entry.action == "admin.force_logout"
    assert entry.user_id == admin.id
    # Platform-admin event — never scoped to the caller's own tenant.
    assert entry.tenant_id is None
    assert entry.resource_id == target.id


@pytest.mark.asyncio
async def test_force_logout_unknown_user_returns_404():
    admin = make_admin()
    session = make_session([None])

    with pytest.raises(HTTPException) as exc_info:
        await AdminService(session=session).force_logout_user(uuid.uuid4(), admin)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_force_logout_audit_log_insert_happens_after_bypass_exits(mocker):
    """Regression for the CRITICAL final-review finding: the AuditLog insert
    must run after `admin_bypass_context` has exited, not while the elevated
    role is still active — quickbite_admin_bypass only has SELECT on
    restaurant.audit_logs, so an INSERT issued inside the block would 500 in
    production with InsufficientPrivilegeError, and worse, only after
    logout_all had already revoked the sessions and committed."""
    admin = make_admin()
    target = make_target_user()
    mocker.patch("app.services.admin_service.AuthService.logout_all", AsyncMock())
    session = make_session([target])
    bypass, state = make_add_tracking_bypass(session)
    mocker.patch("app.services.admin_service.rls.admin_bypass_context", bypass)

    response = await AdminService(session=session).force_logout_user(target.id, admin)

    assert state["add_called_while_active"] is False, (
        "AuditLog was added while still inside admin_bypass_context — this "
        "INSERT would fail with InsufficientPrivilegeError against the real "
        "quickbite_admin_bypass role, which only has SELECT on audit_logs."
    )
    assert response.status == "logged_out"
    entry = added(session, AuditLog)[-1]
    assert entry.action == "admin.force_logout"
    assert entry.resource_id == target.id


# --- list_audit_logs --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_audit_logs_returns_entries_across_tenants():
    entries = [make_audit_log(tenant_id=ADMIN_TENANT_ID), make_audit_log(tenant_id=OTHER_TENANT_ID)]
    session = make_session([entries])

    response = await AdminService(session=session).list_audit_logs(AuditLogFilters())

    assert {e.tenant_id for e in response.entries} == {ADMIN_TENANT_ID, OTHER_TENANT_ID}


@pytest.mark.asyncio
async def test_list_audit_logs_filterable_by_tenant_action_and_date_range():
    filtered = [make_audit_log(tenant_id=OTHER_TENANT_ID, action="force_logout")]
    session = make_session([filtered])

    response = await AdminService(session=session).list_audit_logs(
        AuditLogFilters(
            tenant_id=OTHER_TENANT_ID,
            action="force_logout",
            date_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
    )

    assert len(response.entries) == 1
    assert response.entries[0].action == "force_logout"
    # The filters must actually have reached the query, not just been accepted.
    query = session.execute.await_args.args[0]
    assert "audit_logs.tenant_id" in str(query.whereclause)


# --- trigger_gmb_sync --------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_gmb_sync_enqueues_task_and_audit_logs(mocker):
    admin = make_admin()
    tenant_id = uuid.uuid4()
    delay = mocker.patch("app.workers.tasks.sync_gmb_tenant.delay", MagicMock())
    session = make_session([tenant_id])

    response = await AdminService(session=session).trigger_gmb_sync(tenant_id, admin)

    assert response.status == "sync_queued"
    assert response.tenant_id == tenant_id
    delay.assert_called_once_with(str(tenant_id))
    entry = added(session, AuditLog)[-1]
    assert entry.action == "admin.gmb_sync_triggered"
    assert entry.tenant_id is None
    assert entry.resource_id == tenant_id


@pytest.mark.asyncio
async def test_trigger_gmb_sync_unknown_tenant_returns_404(mocker):
    admin = make_admin()
    mocker.patch("app.workers.tasks.sync_gmb_tenant.delay", MagicMock())
    session = make_session([None])

    with pytest.raises(HTTPException) as exc_info:
        await AdminService(session=session).trigger_gmb_sync(uuid.uuid4(), admin)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "TENANT_NOT_FOUND"


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


# --- override_subscription --------------------------------------------------


@pytest.mark.asyncio
async def test_override_subscription_updates_status_and_audit_logs_reason():
    admin = make_admin()
    sub = make_subscription(status="active")
    original_plan_id = sub.plan_id
    session = make_session([sub])
    payload = SubscriptionOverrideRequest(status="canceled", reason="Customer requested via support ticket #4821")

    response = await AdminService(session=session).override_subscription(
        OTHER_TENANT_ID, payload, admin
    )

    assert response.status == "canceled"
    assert sub.status == "canceled"
    assert sub.plan_id == original_plan_id
    entry = added(session, AuditLog)[-1]
    assert entry.action == "admin.subscription_overridden"
    assert entry.event_metadata["reason"] == "Customer requested via support ticket #4821"
    assert entry.event_metadata["status"] == "canceled"


@pytest.mark.asyncio
async def test_override_subscription_updates_plan_and_trial_end():
    admin = make_admin()
    sub = make_subscription()
    original_status = sub.status
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
    assert sub.status == original_status


@pytest.mark.asyncio
async def test_override_subscription_no_subscription_returns_404():
    admin = make_admin()
    session = make_session([None])
    payload = SubscriptionOverrideRequest(status="active", reason="test")

    with pytest.raises(HTTPException) as exc_info:
        await AdminService(session=session).override_subscription(uuid.uuid4(), payload, admin)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_override_subscription_audit_log_insert_happens_after_bypass_exits(mocker):
    """Regression for the CRITICAL final-review finding: same ordering bug as
    force_logout_user — the AuditLog insert must run after
    admin_bypass_context has exited, never while the elevated role (SELECT
    only on restaurant.audit_logs) is still active."""
    admin = make_admin()
    sub = make_subscription(status="active")
    session = make_session([sub])
    bypass, state = make_add_tracking_bypass(session)
    mocker.patch("app.services.admin_service.rls.admin_bypass_context", bypass)
    payload = SubscriptionOverrideRequest(status="canceled", reason="Regression test")

    response = await AdminService(session=session).override_subscription(
        OTHER_TENANT_ID, payload, admin
    )

    assert state["add_called_while_active"] is False, (
        "AuditLog was added while still inside admin_bypass_context — this "
        "INSERT would fail with InsufficientPrivilegeError against the real "
        "quickbite_admin_bypass role, which only has SELECT on audit_logs."
    )
    assert response.status == "canceled"
    entry = added(session, AuditLog)[-1]
    assert entry.action == "admin.subscription_overridden"
    # The Subscription UPDATE must also be flushed while bypass is still
    # active — payment.subscriptions is RLS-protected and the admin's own
    # tenant context (not the override target's) would otherwise silently
    # filter the UPDATE to zero rows once RESET ROLE has run.
    session.flush.assert_awaited()


# --- get_health_metrics ------------------------------------------------------


@pytest.mark.asyncio
async def test_health_metrics_aggregates_all_six_figures(mocker):
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
    # force_logout_user always writes tenant_id=None (platform-admin event) and
    # stashes the affected tenant in event_metadata["target_tenant_id"] instead
    # — the real query now groups on that JSONB field, so the mocked row must
    # match its shape (a string, not a UUID) or a regression to grouping on
    # the tenant_id column would slip past this test undetected.
    cluster_row = MagicMock(target_tenant_id=str(OTHER_TENANT_ID), count=4)
    session = make_session([[locked_user], [fraud_row], [cluster_row]])

    response = await AdminService(session=session).get_security_flags()

    assert len(response.locked_accounts) == 1
    assert response.locked_accounts[0].failed_login_count == 5
    assert len(response.fraud_flags) == 1
    assert response.fraud_flags[0].stamp_log_id == fraud_row.id
    assert len(response.force_logout_clusters) == 1
    assert response.force_logout_clusters[0].tenant_id == OTHER_TENANT_ID
    assert response.force_logout_clusters[0].count == 4
