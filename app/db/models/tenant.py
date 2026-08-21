"""QuickBite — Tenant model (Doc 2 Table 1, restaurant schema).

Root of everything. Every other table links back here. Source of all RLS
policies. v3.1 adds Indian business registration fields (GSTIN, PAN, owner
phone) stored TIER 3: SHA-256 hash for lookup + AES-256-GCM for display.
Plaintext GSTIN/PAN/phone is never written to a column.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = {"schema": "restaurant"}

    subdomain: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("static.subscription_plans.id"), nullable=True
    )
    # What kind of food business this is — chosen during "Join Us", before the
    # plan, because the category is what decides which plans are offered.
    # Nullable: every tenant created before categories existed has none, and a
    # NULL here means "no category restriction" everywhere it is read.
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("static.business_categories.id"), nullable=True, index=True
    )
    onboarding_state: Mapped[str] = mapped_column(String, default="registered")
    timezone: Mapped[str] = mapped_column(String, default="Asia/Kolkata")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # v3.1 TIER 3 — hash (lookup) + AES-256-GCM (display), never plaintext
    gstin_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    encrypted_gstin: Mapped[str | None] = mapped_column(String, nullable=True)
    pan_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    encrypted_pan: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_phone_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    encrypted_owner_phone: Mapped[str | None] = mapped_column(String, nullable=True)
