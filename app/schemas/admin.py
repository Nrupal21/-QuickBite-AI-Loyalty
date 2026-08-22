"""QuickBite — Super Admin panel schemas: tenants, force-logout, audit logs, GMB sync (ADMIN-01).

Every response here is read by a SUPER_ADMIN only (see require_role(RoleLevel.SUPER_ADMIN)
in app/api/v1/routers/admin.py) — there is no customer- or owner-facing surface
that shares these models.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TenantSummary(BaseModel):
    tenant_id: uuid.UUID
    subdomain: str
    name: str
    onboarding_state: str
    is_active: bool


class TenantListResponse(BaseModel):
    tenants: list[TenantSummary]


class ForceLogoutResponse(BaseModel):
    status: str  # always "logged_out"
    user_id: uuid.UUID


class AuditLogEntry(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    user_id: uuid.UUID | None
    action: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    event_metadata: dict | None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    entries: list[AuditLogEntry]


class AuditLogFilters(BaseModel):
    """Query params for GET /admin/audit-logs — a model so the route stays thin."""

    tenant_id: uuid.UUID | None = None
    action: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class GmbSyncResponse(BaseModel):
    status: Literal["sync_queued"]
    tenant_id: uuid.UUID


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
