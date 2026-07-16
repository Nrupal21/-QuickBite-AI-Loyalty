"""QuickBite — Payment domain new tables, v3.0 DB-02 (payment schema).

PaymentMethod, BillingEvent, Invoice, BillingAuditLog. Core principle:
QuickBite never stores a PAN, CVV, or raw expiry date — every identifier is
a Stripe-issued token. Only card_brand + card_last4 are kept for display
(PCI-permitted). BillingEvent and BillingAuditLog are append-only.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaymentMethod(Base):
    """Stripe payment method token + brand/last4 for display — never the full card."""

    __tablename__ = "payment_methods"
    __table_args__ = {"schema": "payment"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    stripe_payment_method_id: Mapped[str] = mapped_column(String, unique=True)
    card_brand: Mapped[str | None] = mapped_column(String, nullable=True)  # visa / mastercard
    card_last4: Mapped[str | None] = mapped_column(String, nullable=True)  # PCI-permitted display
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class BillingEvent(Base):
    """Immutable log of every Stripe webhook event — append-only, used for reconciliation."""

    __tablename__ = "billing_events"
    __table_args__ = {"schema": "payment"}

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurant.tenants.id"), nullable=True, index=True
    )
    stripe_event_id: Mapped[str] = mapped_column(String, unique=True)  # replay/duplicate guard
    event_type: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Invoice(Base):
    """Read-optimised mirror of Stripe invoices for dashboard display — Stripe is source of truth."""

    __tablename__ = "invoices"
    __table_args__ = {"schema": "payment"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    stripe_invoice_id: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String)  # draft/open/paid/void/uncollectible
    amount_total_inr: Mapped[int] = mapped_column(Integer)  # INR paise
    currency: Mapped[str] = mapped_column(String, default="inr")
    invoice_pdf_url: Mapped[str | None] = mapped_column(String, nullable=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BillingAuditLog(Base):
    """Every read/write touching payment.* — which service, which role, which row. Append-only."""

    __tablename__ = "billing_audit_log"
    __table_args__ = {"schema": "payment"}

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurant.tenants.id"), nullable=True, index=True
    )
    service_name: Mapped[str] = mapped_column(String)  # e.g. billing_service
    db_role: Mapped[str] = mapped_column(String)  # e.g. app_payment_rw
    action: Mapped[str] = mapped_column(String)  # SELECT / INSERT / UPDATE
    table_name: Mapped[str] = mapped_column(String)
    row_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
