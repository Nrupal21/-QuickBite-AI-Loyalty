"""QuickBite — Customer schemas: CustomerRegister, CustomerProfile, CustomerUpdate.

NEW-OTP-03 implements the registration pair. CustomerProfileResponse (GET
/customers/me) is the customer-profile ticket referenced above; CustomerUpdate
is still not implemented.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class CustomerRegister(BaseModel):
    registration_token: str
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr | None = None
    # Required only when the identify/OTP step used an email identifier —
    # Customer.phone_hash is mandatory, so a phone number must exist either
    # way (E.164). Optional here because the phone-identifier path already
    # has one from the pending registration session.
    phone: str | None = Field(default=None, min_length=8, max_length=20)
    whatsapp_opt_in: bool = False
    # Optional third login identifier, checked by /auth/identify alongside
    # phone/email.
    username: str | None = Field(default=None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_.]+$")


class CustomerRegisterResponse(BaseModel):
    status: str = "registered"
    customer_id: str


class LocationStampsOut(BaseModel):
    """Stamp history at one branch — "received stamp from different
    locations" groups by this. `branch_id` lets the client build a link if a
    multi-branch tenant ever needs one; `branch_name`/`branch_address` are
    TIER 1 plaintext (a business's own location name/address, not the
    diner's PII)."""

    branch_id: str
    branch_name: str
    branch_address: str | None = None
    stamp_count: int
    last_scanned_at: datetime


class ReviewDraftOut(BaseModel):
    """One row of "reviews you've sent" — see ReviewDraft's docstring for why
    this is a partial history (only drafts generated while signed in)."""

    id: str
    branch_name: str
    rating: int
    tags: list[str]
    draft_excerpt: str
    created_at: datetime


class CustomerProfileResponse(BaseModel):
    """GET /customers/me — the customer-facing "My Rewards" profile page."""

    customer_id: str
    name: str | None = None
    email: str | None = None
    username: str | None = None
    member_since: datetime
    total_stamps_alltime: int
    current_reward_count: int
    stamps_by_location: list[LocationStampsOut] = Field(default_factory=list)
    recent_review_drafts: list[ReviewDraftOut] = Field(default_factory=list)
