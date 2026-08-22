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
from app.db.models.subscription import Subscription
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.schemas.admin import AuditLogFilters, SubscriptionOverrideRequest
from app.services.admin_service import AdminService

ADMIN_TENANT_ID = uuid.uuid4()
OTHER_TENANT_ID = uuid.uuid4()


def make_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = value if isinstance(value, list) else []
    return result


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
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


# --- list_tenants ---------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tenants_returns_every_tenant_regardless_of_caller():
    """`restaurant.tenants` is RLS-exempt (0009) — every tenant must come back,
    not just the caller's own."""
    mine = make_tenant(name="Marco's")
    other = make_tenant(name="Rival's Diner")
    session = make_session([[mine, other]])

    response = await AdminService(session=session).list_tenants()

    assert {t.name for t in response.tenants} == {"Marco's", "Rival's Diner"}


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
