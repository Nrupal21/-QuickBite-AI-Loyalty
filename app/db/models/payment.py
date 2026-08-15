"""QuickBite — Payment domain new tables, v3.0 DB-02 (payment schema).

PaymentMethod, BillingEvent, Invoice, BillingAuditLog. Core principle:
QuickBite never stores a PAN, CVV, or raw expiry date — every identifier is a
gateway-issued token. Only card_brand + card_last4 are kept for display
(PCI-permitted). BillingEvent and BillingAuditLog are append-only.

Columns are provider-agnostic (`provider` + `provider_*_ref`) so the gateway
is a data value, not a schema commitment. Razorpay is the current gateway.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaymentMethod(Base):
    """Gateway payment-method token + brand/last4 for display — never the full card."""

    __tablename__ = "payment_methods"
    __table_args__ = {"schema": "payment"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String, default="razorpay")
    provider_method_ref: Mapped[str] = mapped_column(String, unique=True)
    card_brand: Mapped[str | None] = mapped_column(String, nullable=True)  # visa / mastercard
    card_last4: Mapped[str | None] = mapped_column(String, nullable=True)  # PCI-permitted display
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class BillingEvent(Base):
    """Immutable log of every gateway webhook event — append-only, used for reconciliation."""

    __tablename__ = "billing_events"
    __table_args__ = {"schema": "payment"}

    # Nullable on purpose: a webhook arrives with no bearer token, and the
    # event is recorded *before* the tenant is resolved so that an unresolvable
    # event is still durably logged rather than silently dropped.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurant.tenants.id"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String, default="razorpay")
    # The idempotency key. Razorpay delivers at-least-once, so the unique
    # constraint — not an application-level "have I seen this?" check — is what
    # makes replay safe under concurrent delivery.
    provider_event_id: Mapped[str] = mapped_column(String, unique=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    # The event's own timestamp at the gateway. Razorpay does not guarantee
    # ordering, so a retried `subscription.updated` can land after
    # `subscription.cancelled`; the handler compares this to drop stale events.
    provider_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Invoice(Base):
    """Read-optimised mirror of gateway invoices for dashboard display."""

    __tablename__ = "invoices"
    __table_args__ = {"schema": "payment"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String, default="razorpay")
    provider_invoice_ref: Mapped[str] = mapped_column(String, unique=True)
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
