"""QuickBite — Super Admin routes: /admin/* (Super Admin only) (ADMIN-01).

Every route is gated by `require_role(RoleLevel.SUPER_ADMIN)` — since
Role.level ranks 1=Super Admin .. 4=Staff and the guard is "at most this
level", passing SUPER_ADMIN itself (not OWNER) is what makes this the one
router in the app that Owners cannot reach.
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import require_role
from app.core.rate_limiter import limiter
from app.core.rbac import RoleLevel
from app.db.base import get_db
from app.db.models.user import User
from app.schemas.admin import (
    AuditLogFilters,
    AuditLogListResponse,
    ForceLogoutResponse,
    GmbSyncResponse,
    TenantListResponse,
)
from app.services.admin_service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/tenants", response_model=TenantListResponse, status_code=status.HTTP_200_OK)
async def list_tenants(
    current_user: User = Depends(require_role(RoleLevel.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> TenantListResponse:
    return await AdminService(session=session).list_tenants()


@router.post(
    "/users/{user_id}/force-logout",
    response_model=ForceLogoutResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("30/hour")
async def force_logout_user(
    request: Request,
    user_id: uuid.UUID,
    current_user: User = Depends(require_role(RoleLevel.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> ForceLogoutResponse:
    return await AdminService(session=session).force_logout_user(user_id, current_user)


@router.get("/audit-logs", response_model=AuditLogListResponse, status_code=status.HTTP_200_OK)
async def list_audit_logs(
    filters: AuditLogFilters = Depends(),  # noqa: B008 — FastAPI's documented pattern for query-param models
    current_user: User = Depends(require_role(RoleLevel.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> AuditLogListResponse:
    return await AdminService(session=session).list_audit_logs(filters)


@router.post(
    "/tenants/{tenant_id}/sync-gmb",
    response_model=GmbSyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("30/hour")
async def sync_gmb(
    request: Request,
    tenant_id: uuid.UUID,
    current_user: User = Depends(require_role(RoleLevel.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> GmbSyncResponse:
    return await AdminService(session=session).trigger_gmb_sync(tenant_id, current_user)
