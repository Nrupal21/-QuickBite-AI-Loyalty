"""QuickBite — Loyalty schemas: ScanRequest, ScanResponse, LoyaltyCard."""

from pydantic import BaseModel, Field

# TODO(LOYALTY-01/02): LoyaltyCard schema for branch/QR display.


class ScanRequest(BaseModel):
    qr_token: str = Field(min_length=10, max_length=100)
    gps_lat: float = Field(ge=-90.0, le=90.0)
    gps_lng: float = Field(ge=-180.0, le=180.0)


class ScanResponse(BaseModel):
    stamp_count: int
    reward_progress: float
    reward_unlocked: bool
    redemption_code: str | None = None
    next_reward_at: int | None = None
