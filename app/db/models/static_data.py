"""QuickBite — Static domain new tables, v3.0 DB-03 (static schema).

FeatureFlag, NotificationTemplate, BusinessCategory, PlanCategory — reference
and lookup data that changes rarely, is never tenant-specific or personally
identifiable, and is safe to cache aggressively. Written only by Super Admin.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FeatureFlag(Base):
    """Platform-wide toggles (e.g. primary AI provider, beta rollout percentages)."""

    __tablename__ = "feature_flags"
    __table_args__ = {"schema": "static"}

    key: Mapped[str] = mapped_column(String, unique=True)  # e.g. ai_provider
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)


class NotificationTemplate(Base):
    """Pre-approved WhatsApp/SMS/email copy referenced by Celery tasks — never hardcoded."""

    __tablename__ = "notification_templates"
    __table_args__ = {"schema": "static"}

    name: Mapped[str] = mapped_column(String, unique=True)  # e.g. reward_unlocked_whatsapp
    channel: Mapped[str] = mapped_column(String)  # whatsapp / sms / email
    subject: Mapped[str | None] = mapped_column(String, nullable=True)  # email only
    body: Mapped[str] = mapped_column(String)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)


class BusinessCategory(Base):
    """The kind of food business a Tenant is — chosen once, during "Join Us".

    Reference data, not tenant data: the list is identical for every tenant,
    changes only when the platform adds a category, and carries nothing
    personal. That puts it in `static` alongside roles and plans (TIER 1
    plaintext under AGENTS.md's classification — a category is as public as a
    storefront sign).

    `slug` is the stable wire value the frontend and analytics key on;
    `display_name` is the only string a human ever sees, so renaming
    "Cloud Kitchen" never invalidates a stored row.
    """

    __tablename__ = "business_categories"
    __table_args__ = {"schema": "static"}

    slug: Mapped[str] = mapped_column(String, unique=True)  # e.g. cloud-kitchen
    display_name: Mapped[str] = mapped_column(String)
    tagline: Mapped[str] = mapped_column(String)
    # Names a drawn icon in the frontend's own SVG set — never an emoji, and
    # never a URL, so the category list stays renderable with no asset fetch.
    icon_key: Mapped[str] = mapped_column(String)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PlanCategory(Base):
    """Which subscription plans a business category may choose from.

    A join table rather than a column on either side: a plan is offered to
    several categories, and a category offers several plans. Absence of any
    row for a category means "every active plan" — see
    BillingService.list_active_plans, which treats an unmapped category as
    unrestricted rather than as an empty pricing page.
    """

    __tablename__ = "plan_categories"
    __table_args__ = (
        UniqueConstraint("plan_id", "category_id", name="uq_plan_category"),
        {"schema": "static"},
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("static.subscription_plans.id"), index=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("static.business_categories.id"), index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
