"""QuickBite — SubscriptionPlan, Subscription, UsageTracking models (Doc 2 Tables 6/7/8).

SubscriptionPlan is seeded reference data (static schema). Subscription and
UsageTracking live in the isolated `payment` schema — reachable only via the
billing service through the app_payment_rw role. Gateway identifiers stored
here are opaque provider tokens, never cardholder data.

Columns are provider-agnostic (`provider` + `provider_*_ref`) rather than
razorpay-specific: Razorpay is the gateway for INR tenants, and keeping the
discriminator leaves room for a second gateway on international ones without
another rename.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SubscriptionPlan(Base):
    """3 seeded rows. feature_limits JSONB avoids schema migrations for limit changes."""

    __tablename__ = "subscription_plans"
    __table_args__ = {"schema": "static"}

    name: Mapped[str] = mapped_column(String, unique=True)  # starter / pro / enterprise
    display_name: Mapped[str] = mapped_column(String)
    price_monthly_inr: Mapped[int] = mapped_column(Integer)  # INR paise, 0 = free
    provider_plan_id: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. plan_xxx
    feature_limits: Mapped[dict] = mapped_column(JSONB, default=dict)
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subscription(Base):
    """One row per tenant. Updated in real time by Razorpay webhooks."""

    __tablename__ = "subscriptions"
    __table_args__ = {"schema": "payment"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurant.tenants.id"), unique=True, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("static.subscription_plans.id"))
    status: Mapped[str] = mapped_column(String)  # trialing/active/past_due/canceled/paused
    provider: Mapped[str] = mapped_column(String, default="razorpay")
    provider_customer_ref: Mapped[str | None] = mapped_column(String, nullable=True)  # cust_xxx
    provider_subscription_ref: Mapped[str | None] = mapped_column(String, nullable=True)  # sub_xxx
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    # Timestamp of the most recent webhook event applied to this row. Razorpay
    # delivers at-least-once with no ordering guarantee, so a retried older
    # event is compared against this and dropped rather than reapplied.
    provider_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageTracking(Base):
    """One row per tenant per month. Real-time counter source for plan gating."""

    __tablename__ = "usage_tracking"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period_year", "period_month", name="uq_usage_tenant_period"),
        {"schema": "payment"},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    period_year: Mapped[int] = mapped_column(Integer)
    period_month: Mapped[int] = mapped_column(Integer)
    ai_responses_used: Mapped[int] = mapped_column(Integer, default=0)
    sms_sent: Mapped[int] = mapped_column(Integer, default=0)
    whatsapp_sent: Mapped[int] = mapped_column(Integer, default=0)
    loyalty_scans_total: Mapped[int] = mapped_column(Integer, default=0)
    warning_sent: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"ai":true,"sms":false}
