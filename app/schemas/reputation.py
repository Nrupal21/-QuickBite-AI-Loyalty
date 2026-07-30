"""QuickBite — Reputation schemas: ReviewCreate, DashboardStats.

REVIEW-01's request schema is the first line of defence for SEC-11: the tag
list is customer-supplied text that ends up inside an LLM prompt, so its
bounds (count, per-tag length) are enforced here, before any service or
provider sees it. prompt_guard then sanitises whatever survives.
"""

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
