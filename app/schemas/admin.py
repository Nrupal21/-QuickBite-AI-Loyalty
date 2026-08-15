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
