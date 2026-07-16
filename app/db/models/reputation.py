"""QuickBite — GMBProfile, CustomerReview, ReviewResponse models (Doc 2 Tables 9/10/11).

All three live in the `restaurant` schema. GMB OAuth tokens are AES-256-GCM
encrypted at rest and decrypted only for API calls. v3.1 adds
token_rotated_at so monitoring can alert if a GMB token goes stale > 50 days.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GMBProfile(Base):
    """OAuth credentials and GMB sync state per branch."""

    __tablename__ = "google_business_profiles"
    __table_args__ = {"schema": "restaurant"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.branches.id"), index=True)
    gmb_account_id: Mapped[str] = mapped_column(String)
    gmb_location_id: Mapped[str] = mapped_column(String)
    encrypted_access_token: Mapped[str] = mapped_column(String)  # AES-256-GCM
    encrypted_refresh_token: Mapped[str] = mapped_column(String)  # AES-256-GCM
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # v3.1 — alerts if the GMB token goes stale > 50 days without rotation
    token_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gmb_sync_cursor: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomerReview(Base):
    """Every review from Google, Yelp, or the QuickBite composer."""

    __tablename__ = "customer_reviews"
    __table_args__ = {"schema": "restaurant"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.branches.id"), index=True)
    source: Mapped[str] = mapped_column(String)  # google / yelp / internal
    external_review_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    rating: Mapped[int] = mapped_column(Integer)
    reviewer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    review_body: Mapped[str | None] = mapped_column(String, nullable=True)
    selected_tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReviewResponse(Base):
    """AI drafts through the approval workflow."""

    __tablename__ = "review_responses"
    __table_args__ = {"schema": "restaurant"}

    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurant.customer_reviews.id"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    ai_draft: Mapped[str] = mapped_column(String)
    final_text: Mapped[str | None] = mapped_column(String, nullable=True)
    approval_state: Mapped[str] = mapped_column(String, default="pending")
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurant.users.id"), nullable=True
    )
    ai_model_used: Mapped[str] = mapped_column(String)  # gpt-4o / gemini-1.5-pro
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
