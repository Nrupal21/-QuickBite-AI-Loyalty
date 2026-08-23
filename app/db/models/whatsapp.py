"""QuickBite — WhatsApp marketing models: WhatsAppBusinessAccount, WhatsAppTemplate,
MarketingCampaign, CampaignRecipient (restaurant schema, RLS-protected).

Per-tenant, bring-your-own Meta WhatsApp Business Account (WABA), connected via
Embedded Signup — one WABA per tenant (brand-level, not per branch: a chain's
WhatsApp presence is one number across locations, unlike GMB's per-location
reviews). `WhatsAppTemplate` mirrors Meta's own approval state so a campaign
can only be built from a template Meta has actually approved. `CampaignRecipient`
is both the per-customer send log and the send-idempotency guard (unique on
campaign_id + customer_id) — a retried fan-out task cannot double-send.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WhatsAppBusinessAccount(Base):
    """The tenant's connected Meta WABA + the system-user token to call it with."""

    __tablename__ = "whatsapp_business_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_whatsapp_business_accounts_tenant"),
        {"schema": "restaurant"},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    waba_id: Mapped[str] = mapped_column(String)
    phone_number_id: Mapped[str] = mapped_column(String)
    business_id: Mapped[str] = mapped_column(String)
    # TIER 3, like every other phone number in this codebase — even though
    # it is the restaurant's own public-facing number.
    phone_hash: Mapped[str] = mapped_column(String, index=True)
    encrypted_display_phone_number: Mapped[str] = mapped_column(String)
    encrypted_access_token: Mapped[str] = mapped_column(String)  # AES-256-GCM
    token_exchanged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality_rating: Mapped[str | None] = mapped_column(String, nullable=True)  # GREEN/YELLOW/RED
    messaging_tier: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. TIER_1K
    webhook_subscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WhatsAppTemplate(Base):
    """One Meta message template, synced from the tenant's WABA. `components`
    is the raw header/body/button structure Meta returns — needed verbatim
    to build a `send_template_message` payload later."""

    __tablename__ = "whatsapp_templates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "name", "language", name="uq_whatsapp_templates_tenant_name_lang"
        ),
        {"schema": "restaurant"},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    waba_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurant.whatsapp_business_accounts.id"), index=True
    )
    meta_template_id: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    language: Mapped[str] = mapped_column(String)  # e.g. en_US
    category: Mapped[str] = mapped_column(String)  # MARKETING / UTILITY / AUTHENTICATION
    status: Mapped[str] = mapped_column(String)  # APPROVED / PENDING / REJECTED
    components: Mapped[list] = mapped_column(JSONB, default=list)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketingCampaign(Base):
    """One outbound WhatsApp broadcast to a segment of opted-in customers."""

    __tablename__ = "marketing_campaigns"
    __table_args__ = {"schema": "restaurant"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurant.branches.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.whatsapp_templates.id"))
    # Segment definition, e.g. {"opted_in_only": true, "last_visit_before": "..."}.
    # campaign_service always ANDs in whatsapp_marketing_opt_in=True regardless
    # of what's stored here — this is a filter on top of that hard floor, not
    # a substitute for it.
    audience_filter: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String, default="draft")  # draft/scheduled/sending/completed/failed
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.users.id"))
    recipients_total: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, default=0)
    read_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)


class CampaignRecipient(Base):
    """One customer's send within a campaign — status tracking, and the
    (campaign_id, customer_id) uniqueness is what makes the Celery fan-out
    task idempotent on retry."""

    __tablename__ = "campaign_recipients"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "customer_id", name="uq_campaign_recipients_campaign_customer"
        ),
        {"schema": "restaurant"},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurant.marketing_campaigns.id"), index=True
    )
    # Cross-schema reference to customer.customers, same pattern reward_redemptions
    # already uses for the same reason (traceability only, not an RLS boundary
    # since customer.customers has its own tenant-scoped RLS).
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer.customers.id"), index=True)
    wa_message_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default="queued")  # queued/sent/delivered/read/failed
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
