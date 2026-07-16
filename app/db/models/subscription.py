"""QuickBite — SubscriptionPlan, Subscription, UsageTracking models (Doc 2 Tables 6/7/8).

SubscriptionPlan is seeded reference data (static schema). Subscription and
UsageTracking live in the isolated `payment` schema — reachable only via the
billing service through the app_payment_rw role. Stripe identifiers stored
here are opaque Stripe tokens, never cardholder data.
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
    stripe_price_id: Mapped[str | None] = mapped_column(String, nullable=True)
    feature_limits: Mapped[dict] = mapped_column(JSONB, default=dict)
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subscription(Base):
    """One row per tenant. Updated in real time by Stripe webhooks."""

    __tablename__ = "subscriptions"
    __table_args__ = {"schema": "payment"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurant.tenants.id"), unique=True, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("static.subscription_plans.id"))
    status: Mapped[str] = mapped_column(String)  # trialing/active/past_due/canceled/paused
    stripe_customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String, nullable=True)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)


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
