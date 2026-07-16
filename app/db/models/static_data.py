"""QuickBite — Static domain new tables, v3.0 DB-03 (static schema).

FeatureFlag and NotificationTemplate. Reference/lookup data that changes
rarely, is never tenant-specific or personally identifiable, and is safe to
cache aggressively. Written only by Super Admin.
"""

from sqlalchemy import Boolean, String
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
