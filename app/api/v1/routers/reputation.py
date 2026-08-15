"""QuickBite — Reputation routes: /reviews/*, /dashboard/*, /gmb/*.

REVIEW-01 implements /reviews/generate. REVIEW-02 adds the approve/reject
approval workflow below. GMB OAuth + sync (REVIEW-03) and the dashboard
endpoints (DASH-01/02) arrive with their own tickets.
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import require_role
from app.core.rate_limiter import limiter
from app.core.rbac import RoleLevel
from app.db.base import get_db
from app.db.models.user import User
from app.schemas.reputation import (
    ReviewApproveRequest,
    ReviewDraftResponse,
    ReviewGenerateRequest,
    ReviewRejectRequest,
    ReviewResponseOut,
)
from app.services import response_service, review_service

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


@router.patch(
    "/{review_response_id}/approve",
    response_model=ReviewResponseOut,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def approve_review_response(
    request: Request,
    review_response_id: uuid.UUID,
    payload: ReviewApproveRequest,
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> ReviewResponseOut:
    """Approve an AI-drafted response. Manager+ only — Staff gets 403 (REVIEW-02)."""
    return await response_service.approve_response(
        session, review_response_id, current_user, payload.final_text
    )


@router.patch(
    "/{review_response_id}/reject",
    response_model=ReviewResponseOut,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def reject_review_response(
    request: Request,
    review_response_id: uuid.UUID,
    payload: ReviewRejectRequest,
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> ReviewResponseOut:
    """Reject a draft, triggering regeneration with a variation prompt (REVIEW-02)."""
    return await response_service.reject_response(session, review_response_id, payload.reason)
