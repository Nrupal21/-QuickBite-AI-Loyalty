"""QuickBite — Reputation schemas: ReviewCreate, DashboardStats.

REVIEW-01's request schema is the first line of defence for SEC-11: the tag
list is customer-supplied text that ends up inside an LLM prompt, so its
bounds (count, per-tag length) are enforced here, before any service or
provider sees it. prompt_guard then sanitises whatever survives.

REVIEW-02 adds the approve/reject schemas for the response-approval
workflow. `final_text` on approve is optional — a Manager approving the
AI draft as-is sends nothing; one who edited it first sends the edited text.

REVIEW-03 adds the GMB OAuth connect/disconnect schemas. `GmbProfileStatusOut`
is deliberately thin (connection state + sync freshness, not tokens or raw
Google ids) — it is what a dashboard reconnect banner needs, nothing more.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# Long enough for "excellent butter chicken", short enough that a tag cannot
# smuggle a paragraph of instructions. Mirrors prompt_guard.MAX_TAG_LENGTH.
MAX_TAG_LENGTH = 60
MAX_TAGS = 5


class ReviewGenerateRequest(BaseModel):
    """POST /reviews/generate — public, no auth (the diner just scanned a QR)."""

    branch_qr_token: str = Field(min_length=10, max_length=100)
    rating: int = Field(ge=1, le=5)
    tags: list[str] = Field(max_length=MAX_TAGS)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        """Reject an empty list using REVIEW-01's exact wording.

        `min_length=1` on the Field would 422 correctly but with Pydantic's
        generic "List should have at least 1 item". The acceptance criterion
        names the message the diner sees, so it is raised explicitly.
        """
        cleaned = [tag.strip() for tag in value if tag and tag.strip()]
        if not cleaned:
            msg = "Please select at least 1 tag"
            raise ValueError(msg)
        for tag in cleaned:
            if len(tag) > MAX_TAG_LENGTH:
                msg = f"Each tag must be {MAX_TAG_LENGTH} characters or fewer"
                raise ValueError(msg)
        return cleaned


class ReviewDraftResponse(BaseModel):
    """`cached` and `model` are returned for observability, not decoration —
    two of REVIEW-01's criteria are about which path served the request."""

    draft: str
    model: str  # gpt-4o | gemini-1.5-pro
    cached: bool
    tags: list[str]  # post-sanitisation, so the caller sees what was actually used


class ReviewApproveRequest(BaseModel):
    """PATCH /reviews/{id}/approve. Manager+ only (REVIEW-02)."""

    final_text: str | None = Field(default=None, max_length=2000)

    @field_validator("final_text")
    @classmethod
    def strip_final_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            msg = "final_text cannot be blank"
            raise ValueError(msg)
        return stripped


class ReviewRejectRequest(BaseModel):
    """PATCH /reviews/{id}/reject. Manager+ only (REVIEW-02).

    `reason` is optional context for the regeneration prompt — not required,
    since "this doesn't sound like us" is a valid reject with nothing more to say.
    """

    reason: str | None = Field(default=None, max_length=500)


class ReviewResponseOut(BaseModel):
    """A `review_responses` row, shaped for the dashboard/approval UI."""

    id: uuid.UUID
    review_id: uuid.UUID
    ai_draft: str
    final_text: str | None
    approval_state: str  # pending | approved | rejected | posted
    ai_model_used: str
    approved_by_user_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewOut(BaseModel):
    """One `customer_reviews` row for the dashboard's Reviews page, with its
    `review_responses` row (if any) nested — a diner's review needs no
    approval (it posted straight to Google), the AI-drafted *reply* is what
    review_responses/approve/reject gate, so this is the one read the page
    needs rather than two separate list calls."""

    id: uuid.UUID
    branch_id: uuid.UUID
    branch_name: str
    source: str  # google | yelp | internal
    rating: int
    reviewer_name: str | None
    review_body: str | None
    sentiment_score: float | None
    reviewed_at: datetime
    response: ReviewResponseOut | None = None

    model_config = {"from_attributes": True}


class GmbConnectResponse(BaseModel):
    """GET /gmb/connect — send the owner's browser here to start consent."""

    authorize_url: str


class GmbDisconnectRequest(BaseModel):
    """POST /gmb/disconnect. Owner+ only (REVIEW-03)."""

    branch_id: uuid.UUID


class GmbProfileStatusOut(BaseModel):
    """The connect/callback/disconnect response shape — a dashboard
    reconnect banner keys off `is_connected` and `last_synced_at`."""

    branch_id: uuid.UUID
    is_connected: bool
    last_synced_at: datetime | None
    token_rotated_at: datetime | None

    model_config = {"from_attributes": True}
