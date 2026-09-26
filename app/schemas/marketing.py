"""QuickBite — WhatsApp marketing schemas: WABA connect/status, templates, campaigns.

`WhatsAppStatusOut` is deliberately thin (connection state + Meta's own
quality signals, never the access token) — same principle GmbProfileStatusOut
follows in schemas/reputation.py. `WhatsAppConnectRequest`'s four fields are
exactly what the Embedded Signup JS SDK hands back via `window.postMessage`
on success — the frontend forwards them verbatim, nothing is derived server-side.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class WhatsAppEmbeddedSignupConfigOut(BaseModel):
    """GET /marketing/whatsapp/embedded-signup-config — what the dashboard's
    Embedded Signup JS SDK needs to launch the Facebook popup."""

    app_id: str
    config_id: str


class WhatsAppConnectRequest(BaseModel):
    """POST /marketing/whatsapp/connect. Owner+ only."""

    code: str = Field(min_length=1)
    waba_id: str = Field(min_length=1)
    phone_number_id: str = Field(min_length=1)
    business_id: str = Field(min_length=1)


class WhatsAppStatusOut(BaseModel):
    is_connected: bool
    display_phone_number: str | None = None
    quality_rating: str | None = None
    messaging_tier: str | None = None
    webhook_subscribed: bool
    connected_at: datetime | None = None


class WhatsAppTemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    language: str
    category: str
    status: str
    last_synced_at: datetime

    model_config = {"from_attributes": True}


class CampaignCreateRequest(BaseModel):
    """POST /marketing/campaigns. Owner+ only.

    `audience_filter` narrows *within* opted-in customers — campaign_service
    always hard-filters on whatsapp_marketing_opt_in=True regardless of what's
    passed here (see its resolve_audience docstring). `min_total_stamps` is
    the one supported key for v1.
    """

    name: str = Field(min_length=1, max_length=100)
    template_id: uuid.UUID
    branch_id: uuid.UUID | None = None
    audience_filter: dict = Field(default_factory=dict)


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str  # draft/scheduled/sending/completed/failed
    template_id: uuid.UUID
    branch_id: uuid.UUID | None
    recipients_total: int
    sent_count: int
    delivered_count: int
    read_count: int
    failed_count: int
    scheduled_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
