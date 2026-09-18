"""QuickBite — Loyalty schemas: ScanRequest, ScanResponse, reward programs, redemption (LOYALTY-04)."""

import uuid
from datetime import datetime
from typing import Literal

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
    validity_days: int | None = None
    # NICE-01: set on every 5th valid stamp. `scratch_card_id` present means
    # "show the scratch screen for this card"; `scratch_card_locked` means
    # "the 5th stamp landed, but this tenant's plan doesn't include
    # scratch_cards" — the acceptance criterion's "402 upgrade prompt"
    # signal, surfaced here rather than as an actual 402 status on the scan
    # itself, since the stamp/geofence/rate-limit result is still a genuine
    # 200 regardless of the scratch-card feature gate.
    scratch_card_id: uuid.UUID | None = None
    scratch_card_locked: bool = False


class RewardProgramCreateRequest(BaseModel):
    branch_id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)
    stamps_required: int = Field(ge=1, le=100)
    reward_type: Literal["free_item", "percentage_off", "fixed_off"]
    reward_value: str = Field(min_length=1, max_length=100)
    validity_days: int = Field(ge=1, le=365)


class RewardProgramUpdateRequest(BaseModel):
    """PATCH /loyalty/reward-programs/{id}. Every field optional — only what
    the caller sends changes. `is_active` alone is how a program is paused
    or resumed; there is no separate archive/delete endpoint (LOYALTY-04's
    RewardRedemption rows FK onto reward_program_id, so a hard delete would
    orphan redemption history — same "never DELETE, flip a flag" rule
    team_service.deactivate already follows for staff)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    stamps_required: int | None = Field(default=None, ge=1, le=100)
    reward_type: Literal["free_item", "percentage_off", "fixed_off"] | None = None
    reward_value: str | None = Field(default=None, min_length=1, max_length=100)
    validity_days: int | None = Field(default=None, ge=1, le=365)
    is_active: bool | None = None


class RewardProgramResponse(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    branch_name: str
    name: str
    stamps_required: int
    reward_type: str
    reward_value: str
    validity_days: int
    is_active: bool


class RedeemCodeResponse(BaseModel):
    status: str  # always "redeemed"
    code: str
    customer_id: uuid.UUID


class PrizePoolCreateRequest(BaseModel):
    branch_id: uuid.UUID
    prize_label: str = Field(min_length=1, max_length=100)


class PrizePoolResponse(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    branch_name: str
    prize_label: str
    is_active: bool


class ScratchCardRevealRequest(BaseModel):
    """`scratched_percentage` is the canvas mechanic's own estimate of erased
    area (client-computed via `getImageData` sampling) — trusted only as a
    threshold gate, same trust level as any other client-reported UI state;
    the actual prize was already decided server-side at card creation."""

    scratched_percentage: float = Field(ge=0.0, le=1.0)


class ScratchCardRevealResponse(BaseModel):
    prize_label: str
    redemption_code: str
    revealed_at: datetime
