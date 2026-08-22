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
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.db import rls
from app.db.models.audit import AuditLog
from app.db.models.loyalty import StampLog
from app.db.models.subscription import Subscription
from app.db.models.tenant import Tenant
from app.db.models.user import Session as UserSession
from app.db.models.user import User
from app.schemas.admin import (
    ApiUsageDay,
    ApiUsageResponse,
    AuditLogEntry,
    AuditLogFilters,
    AuditLogListResponse,
    ForceLogoutCluster,
    ForceLogoutResponse,
    FraudFlag,
    GmbSyncResponse,
    HealthMetricsResponse,
    LockedAccountFlag,
    SecurityFlagsResponse,
    SessionListResponse,
    SessionSummary,
    SubscriptionOverrideRequest,
    SubscriptionOverrideResponse,
    TenantListResponse,
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

_SUBSCRIPTION_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail={
        "error": {
            "code": "SUBSCRIPTION_NOT_FOUND",
            "message": "This tenant has no subscription to override.",
        }
    },
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
