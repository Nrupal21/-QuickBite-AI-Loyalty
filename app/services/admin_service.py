"""QuickBite — Super Admin panel: tenants, force-logout, audit logs, GMB sync (ADMIN-01).

Every method here is reachable only via `require_role(RoleLevel.SUPER_ADMIN)`
(app/api/v1/routers/admin.py) — that dependency is what guarantees the caller
is who Doc 3 means by "Super Admin", not anything in this module.

`list_tenants` needs no BYPASSRLS: `restaurant.tenants` was never RLS-protected
(migration 0009). `force_logout_user` and `list_audit_logs` read/write rows
scoped to a tenant the caller's own JWT does not carry, so they run inside
`rls.admin_bypass_context` — see that function's docstring for why this is
safe on a pooled connection. `trigger_gmb_sync` only reads the RLS-exempt
Tenant row and hands off to a Celery task that scopes itself to the one
tenant it was given (`app.workers.tasks.sync_gmb_tenant`), so it needs no
bypass either.

Every action here writes its own AuditLog with `tenant_id=None` — the
existing `tenant_isolation_audit_logs` policy already admits NULL-tenant rows
unconditionally (they are platform-admin events, per audit.py's docstring),
so these inserts never need the bypass. The affected tenant/user is recorded
via `resource_id` instead.
"""

import uuid

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import rls
from app.db.models.audit import AuditLog
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.schemas.admin import (
    AuditLogEntry,
    AuditLogFilters,
    AuditLogListResponse,
    ForceLogoutResponse,
    GmbSyncResponse,
    TenantListResponse,
    TenantStatusUpdateRequest,  # noqa: F401 — imported for type-hint completeness, matching this file's style of importing every schema it touches; the route unpacks the payload before calling this service.
    TenantStatusUpdateResponse,
    TenantSummary,
)
from app.services.auth_service import AuthService

logger = structlog.get_logger(__name__)

_USER_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail={"error": {"code": "USER_NOT_FOUND", "message": "No such user."}},
)

_TENANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail={"error": {"code": "TENANT_NOT_FOUND", "message": "No such tenant."}},
)


class AdminService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_tenants(self) -> TenantListResponse:
        result = await self.session.execute(select(Tenant).order_by(Tenant.name))
        tenants = result.scalars().all()
        return TenantListResponse(
            tenants=[
                TenantSummary(
                    tenant_id=tenant.id,
                    subdomain=tenant.subdomain,
                    name=tenant.name,
                    onboarding_state=tenant.onboarding_state,
                    is_active=tenant.is_active,
                )
                for tenant in tenants
            ]
        )

    async def force_logout_user(
        self, target_user_id: uuid.UUID, admin: User
    ) -> ForceLogoutResponse:
        async with rls.admin_bypass_context(self.session):
            result = await self.session.execute(select(User).where(User.id == target_user_id))
            target = result.scalar_one_or_none()
            if target is None:
                raise _USER_NOT_FOUND

            # Revokes every active Session row and bumps tokens_valid_from, so
            # a still-unexpired local access token is rejected on its next use
            # too (see _assert_not_globally_revoked in api/v1/dependencies/auth.py).
            await AuthService(self.session).logout_all(target.id)

            self.session.add(
                AuditLog(
                    tenant_id=None,
                    user_id=admin.id,
                    action="admin.force_logout",
                    resource_type="user",
                    resource_id=target.id,
                    event_metadata={"target_tenant_id": str(target.tenant_id)},
                )
            )
            await self.session.commit()

        logger.info(
            "admin.force_logout", admin_id=str(admin.id), target_user_id=str(target.id)
        )
        return ForceLogoutResponse(status="logged_out", user_id=target.id)

    async def list_audit_logs(self, filters: AuditLogFilters) -> AuditLogListResponse:
        async with rls.admin_bypass_context(self.session):
            query = select(AuditLog)
            if filters.tenant_id is not None:
                query = query.where(AuditLog.tenant_id == filters.tenant_id)
            if filters.action is not None:
                query = query.where(AuditLog.action == filters.action)
            if filters.date_from is not None:
                query = query.where(AuditLog.created_at >= filters.date_from)
            if filters.date_to is not None:
                query = query.where(AuditLog.created_at <= filters.date_to)
            query = (
                query.order_by(AuditLog.created_at.desc())
                .limit(filters.limit)
                .offset(filters.offset)
            )
            result = await self.session.execute(query)
            entries = result.scalars().all()

        return AuditLogListResponse(
            entries=[
                AuditLogEntry(
                    id=entry.id,
                    tenant_id=entry.tenant_id,
                    user_id=entry.user_id,
                    action=entry.action,
                    resource_type=entry.resource_type,
                    resource_id=entry.resource_id,
                    event_metadata=entry.event_metadata,
                    created_at=entry.created_at,
                )
                for entry in entries
            ]
        )

    async def trigger_gmb_sync(self, tenant_id: uuid.UUID, admin: User) -> GmbSyncResponse:
        # Local import — avoids a service/task import cycle, same as
        # response_service.approve_response importing post_approved_response.
        from app.workers.tasks import sync_gmb_tenant  # noqa: PLC0415

        result = await self.session.execute(select(Tenant.id).where(Tenant.id == tenant_id))
        if result.scalar_one_or_none() is None:
            raise _TENANT_NOT_FOUND

        sync_gmb_tenant.delay(str(tenant_id))

        self.session.add(
            AuditLog(
                tenant_id=None,
                user_id=admin.id,
                action="admin.gmb_sync_triggered",
                resource_type="tenant",
                resource_id=tenant_id,
                event_metadata=None,
            )
        )
        await self.session.commit()

        logger.info(
            "admin.gmb_sync.triggered", admin_id=str(admin.id), tenant_id=str(tenant_id)
        )
        return GmbSyncResponse(status="sync_queued", tenant_id=tenant_id)

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
