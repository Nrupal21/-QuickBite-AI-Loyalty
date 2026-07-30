"""QuickBite — Reputation routes: /reviews/*, /dashboard/*, /gmb/*.

REVIEW-01 implements /reviews/generate. GMB sync (REVIEW-02/03) and the
dashboard endpoints (DASH-01/02) arrive with their own tickets.
"""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import limiter
from app.db.base import get_db
from app.schemas.reputation import ReviewDraftResponse, ReviewGenerateRequest
from app.services import review_service

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("/generate", response_model=ReviewDraftResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def generate_review(
    request: Request,
    payload: ReviewGenerateRequest,
    session: AsyncSession = Depends(get_db),
) -> ReviewDraftResponse:
    """Draft a review from a rating and tags. Public — the diner has no session.

    Unauthenticated by design (REVIEW-01: "No auth required"), so the QR token
    is the only credential and this rate limit is the only brake on someone
    burning a tenant's AI budget. Both matter: a leaked QR token is a bill.
    """
    return await review_service.generate_review_draft(payload, session)
