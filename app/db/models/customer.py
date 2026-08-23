"""QuickBite — Customer model (Doc 2 Table 15, customer schema).

Loyalty member profiles. All PII (phone/email/name) is TIER 3: SHA-256
hashed lookup index + AES-256-GCM encrypted value — no plaintext PII is
ever queryable. Only the public OTP and loyalty-scan endpoints write to
this schema; dashboard analytics reads through anonymising views.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_hash", name="uq_customers_tenant_phone"),
        UniqueConstraint("tenant_id", "email_hash", name="uq_customers_tenant_email"),
        UniqueConstraint("tenant_id", "username_hash", name="uq_customers_tenant_username"),
        {"schema": "customer"},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    phone_hash: Mapped[str] = mapped_column(String, index=True)
    encrypted_phone: Mapped[str] = mapped_column(String)
    email_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    encrypted_email: Mapped[str | None] = mapped_column(String, nullable=True)
    # Optional third login identifier (identify-first login) — TIER 3 like
    # phone/email, tenant-scoped unique like the other two.
    username_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    encrypted_username: Mapped[str | None] = mapped_column(String, nullable=True)
    encrypted_name: Mapped[str | None] = mapped_column(String, nullable=True)
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    # Separate from whatsapp_opt_in (transactional alerts, e.g. reward
    # unlocked): Meta requires distinct consent for MARKETING-category
    # template sends, and a customer may want one without the other.
    whatsapp_marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    total_stamps_alltime: Mapped[int] = mapped_column(Integer, default=0)
    current_reward_count: Mapped[int] = mapped_column(Integer, default=0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    otp_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Revocation watermark for Firebase/Supabase customer tokens. Customers
    # have no `sessions` row and no jti blocklist, so for them this is the
    # *only* server-side revocation mechanism.
    tokens_valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ReviewDraft(Base):
    """One AI-drafted review a logged-in customer generated (migration 0013).

    Powers the customer profile page's "reviews you've sent" list. Only
    written when a customer session exists at draft time — most QR scans are
    fully anonymous by design (REVIEW-01), so this is necessarily a partial
    history, not every review the customer has ever drafted or posted.
    """

    __tablename__ = "review_drafts"
    __table_args__ = {"schema": "customer"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.branches.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer.customers.id"), index=True
    )
    rating: Mapped[int] = mapped_column(Integer)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    draft_excerpt: Mapped[str] = mapped_column(String(280))
