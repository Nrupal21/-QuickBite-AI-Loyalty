"""QuickBite — Loyalty routes: scan, reward programs, redemption (LOYALTY-03/04),
analytics (DASH-02), scratch cards + prize pool (NICE-01).

reward-programs -> OWNER (Doc 3: owner configures loyalty rules)
redeem/{code}   -> STAFF (till-side verification, any staff member)
analytics       -> MANAGER (business-intelligence view, same rank as
                   team_service.list_members — a Manager supervises the
                   floor, Staff do not need the numbers)
prize-pool      -> OWNER to create, MANAGER+ to list (same split as
                   reward-programs above)
scratch-cards/{id}/reveal -> customer session (the diner revealing their own
                   card), not a staff role at all

TODO(LOYALTY-01/02): /loyalty/card/{id}, /loyalty/menu/{branch_id}
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import require_role
from app.api.v1.dependencies.customer_auth import get_current_customer, get_current_customer_optional
from app.core.rate_limiter import limiter
from app.core.rbac import RoleLevel
from app.db.base import get_db
from app.db.models.customer import Customer
from app.db.models.user import User
from app.schemas.dashboard import LoyaltyAnalyticsResponse
from app.schemas.loyalty import (
    PrizePoolCreateRequest,
    PrizePoolResponse,
    RedeemCodeResponse,
    RewardProgramCreateRequest,
    RewardProgramResponse,
    RewardProgramUpdateRequest,
    ScanRequest,
    ScanResponse,
    ScratchCardRevealRequest,
    ScratchCardRevealResponse,
)
from app.services import dashboard_service
from app.services.loyalty_service import LoyaltyService

router = APIRouter(prefix="/loyalty", tags=["loyalty"])


@router.post("/scan", response_model=ScanResponse, status_code=200)
@limiter.limit("6/hour")
async def scan_loyalty_qr(
    request: Request,
    body: ScanRequest,
    session: AsyncSession = Depends(get_db),
    current_customer: Customer | None = Depends(get_current_customer_optional),
) -> ScanResponse:
    """
    Process a loyalty QR scan. Validates geofence, checks rate limits,
    logs the stamp, and returns updated loyalty status.

    Rate limit: 6 scans per hour per IP.
    """
    service = LoyaltyService(session=session)
    return await service.process_scan(
        qr_token=body.qr_token,
        gps_lat=body.gps_lat,
        gps_lng=body.gps_lng,
        client_ip=get_remote_address(request),
        customer=current_customer,
    )


@router.post(
    "/reward-programs", response_model=RewardProgramResponse, status_code=status.HTTP_201_CREATED
)
async def create_reward_program(
    body: RewardProgramCreateRequest,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> RewardProgramResponse:
    return await LoyaltyService(session=session).create_reward_program(body, current_user)


@router.get(
    "/reward-programs", response_model=list[RewardProgramResponse], status_code=status.HTTP_200_OK
)
async def list_reward_programs(
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> list[RewardProgramResponse]:
    """Manager+ — same rank as GET /branches and GET /loyalty/analytics."""
    return await LoyaltyService(session=session).list_reward_programs()


@router.patch(
    "/reward-programs/{program_id}",
    response_model=RewardProgramResponse,
    status_code=status.HTTP_200_OK,
)
async def update_reward_program(
    program_id: uuid.UUID,
    body: RewardProgramUpdateRequest,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> RewardProgramResponse:
    """Owner+ — same rank as creating a program. `is_active: false` is how a
    program is paused; there is no delete endpoint (see the schema's own
    docstring)."""
    return await LoyaltyService(session=session).update_reward_program(
        program_id, body, current_user
    )


@router.post("/redeem/{code}", response_model=RedeemCodeResponse, status_code=status.HTTP_200_OK)
async def redeem_reward_code(
    code: str,
    current_user: User = Depends(require_role(RoleLevel.STAFF)),
    session: AsyncSession = Depends(get_db),
) -> RedeemCodeResponse:
    return await LoyaltyService(session=session).redeem_code(code, current_user)


@router.get("/analytics", response_model=LoyaltyAnalyticsResponse, status_code=status.HTTP_200_OK)
async def get_loyalty_analytics(
    branch_id: uuid.UUID | None = None,
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> LoyaltyAnalyticsResponse:
    return await dashboard_service.get_loyalty_analytics(session, branch_id=branch_id)


@router.post(
    "/prize-pool", response_model=PrizePoolResponse, status_code=status.HTTP_201_CREATED
)
async def create_prize_pool_entry(
    body: PrizePoolCreateRequest,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> PrizePoolResponse:
    """Owner+ — same rank as creating a reward program (NICE-01)."""
    return await LoyaltyService(session=session).create_prize_pool_entry(body, current_user)


@router.get(
    "/prize-pool", response_model=list[PrizePoolResponse], status_code=status.HTTP_200_OK
)
async def list_prize_pool_entries(
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> list[PrizePoolResponse]:
    """Manager+ — same rank as GET /loyalty/reward-programs."""
    return await LoyaltyService(session=session).list_prize_pool_entries()


@router.post(
    "/scratch-cards/{card_id}/reveal",
    response_model=ScratchCardRevealResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("30/hour")
async def reveal_scratch_card(
    request: Request,
    card_id: uuid.UUID,
    body: ScratchCardRevealRequest,
    current_customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_db),
) -> ScratchCardRevealResponse:
    """Customer-facing — the diner revealing their own scratch card, not a
    staff/owner action. `get_current_customer` (required, not `_optional`)
    because a scratch card always belongs to a known customer_id
    (`_maybe_create_scratch_card` never mints one for an anonymous scan)."""
    return await LoyaltyService(session=session).reveal_scratch_card(
        card_id, current_customer, body.scratched_percentage
    )
