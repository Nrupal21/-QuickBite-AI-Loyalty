"""QuickBite — AuditLog model (Doc 2 Table 14, restaurant schema).

Immutable, append-only, covers every security event. tenant_id is NULL for
platform-admin events, so the RLS policy also passes NULL-tenant rows.
v3.1: ip_address_hash replaces raw ip_address (TIER 2).

SecurityEvent (real-time alerting rows for SEC-29 monitoring) is not built
yet — it arrives with the SEC monitoring tickets.
"""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"schema": "restaurant"}

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurant.tenants.id"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurant.users.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String, index=True)  # login_success / otp_request / ...
    resource_type: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # v3.1 TIER 2 — SHA-256 of source IP, never the raw address
    ip_address_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
