"""QuickBite — User, Role, Session models (Doc 2 Tables 2/3/4).

User and Session live in the `restaurant` schema (tenant-scoped, RLS).
Role is seeded reference data and lives in the `static` schema.

v3.1 changes:
- users: staff mobile stored TIER 3 (phone_hash + encrypted_phone) — closes
  the social-engineering / SIM-swap gap.
- sessions: ip_address_hash replaces raw ip_address (TIER 2 — raw IPs are
  PII in most jurisdictions).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Role(Base):
    """4 seeded rows. Permissions in JSONB so new ones need no schema migration."""

    __tablename__ = "roles"
    __table_args__ = {"schema": "static"}

    name: Mapped[str] = mapped_column(String, unique=True)  # SUPER_ADMIN/OWNER/MANAGER/STAFF
    level: Mapped[int] = mapped_column(Integer)  # 1=Super, 2=Owner, 3=Manager, 4=Staff
    permissions: Mapped[dict] = mapped_column(JSONB, default=dict)
    mfa_required: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base):
    """Owner/Manager/Staff login accounts. NOT loyalty customers (customer.customers)."""

    __tablename__ = "users"
    __table_args__ = {"schema": "restaurant"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("static.roles.id"))
    email_hash: Mapped[str] = mapped_column(String, unique=True)
    encrypted_email: Mapped[str] = mapped_column(String)
    # v3.1 TIER 3 — staff mobile: hash for lookup + AES-256-GCM for display
    phone_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    encrypted_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    # Optional third login identifier (identify-first login) — TIER 3 like
    # email, globally unique for the same reason email is.
    username_hash: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    encrypted_username: Mapped[str | None] = mapped_column(String, nullable=True)
    hashed_password: Mapped[str] = mapped_column(String)  # bcrypt rounds=12
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret: Mapped[str | None] = mapped_column(String, nullable=True)  # AES-256-GCM
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Soft delete — an Owner removing a team member flips this rather than
    # deleting the row, because audit_logs.user_id points at it and a security
    # trail that loses its actor is worthless. get_current_user() and login()
    # both reject is_active=False.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Revocation watermark for externally-issued tokens (Supabase/Firebase).
    # Those providers expose no server-side per-session handle, so the local
    # `revoked_jti:` blocklist cannot reach them; instead logout and
    # deactivation move this forward and any token issued before it is refused.
    # Unavoidably logout-*all* semantics for external sessions.
    tokens_valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Session(Base):
    """Active Owner/Staff logins. Enables 'logout all devices'."""

    __tablename__ = "sessions"
    __table_args__ = {"schema": "restaurant"}

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.users.id"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String, unique=True)
    # v3.1 TIER 2 — SHA-256 of source IP, never the raw address
    ip_address_hash: Mapped[str] = mapped_column(String)
    user_agent: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
