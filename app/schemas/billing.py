"""Schemas for /billing/* — subscription status and Razorpay checkout.

The webhook route (`/webhooks/razorpay`) deliberately has no request schema:
it reads the raw body itself (`await request.body()`) so the HMAC signature is
verified over the exact bytes Razorpay sent, before Pydantic — or anything
else — touches the payload.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SubscriptionStatusResponse(BaseModel):
    """Current billing state for the caller's tenant."""

    status: str  # trialing | active | past_due | canceled | paused | none
    plan_name: str | None = None
    provider: str | None = None
    current_period_end: datetime | None = None
    trial_ends_at: datetime | None = None
    cancel_at_period_end: bool = False


class CheckoutRequest(BaseModel):
    plan_id: uuid.UUID


class CheckoutResponse(BaseModel):
    """Everything the client-side Razorpay Checkout widget needs.

    `key_id` is the publishable key (RAZORPAY_KEY_ID) — safe to return to the
    client; the secret never leaves the server.
    """

    status: Literal["checkout_created"] = "checkout_created"
    provider_subscription_ref: str
    key_id: str
    short_url: str | None = None


class WebhookAckResponse(BaseModel):
    """Ack body for /webhooks/razorpay. Razorpay only checks the HTTP status,
    but a structured body makes manual replay debugging far less guesswork."""

    status: Literal["processed", "duplicate", "received"]
    tenant_resolved: bool = Field(default=True)
