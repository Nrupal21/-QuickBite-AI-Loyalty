"""QuickBite — Reputation routes: /reviews/*, /gmb/*.

REVIEW-01 implements /reviews/generate. REVIEW-02 adds the approve/reject
approval workflow below. REVIEW-03 adds GMB OAuth connect/disconnect. The
dashboard endpoints (DASH-01/02) arrive with their own ticket.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import require_role
from app.api.v1.dependencies.customer_auth import get_current_customer_optional
from app.api.v1.dependencies.subscription import check_subscription_tier
from app.core.rate_limiter import limiter
from app.core.rbac import RoleLevel
from app.db.base import get_db
from app.db.models.branch import Branch
from app.db.models.customer import Customer
from app.db.models.user import User
from app.schemas.reputation import (
    GmbConnectResponse,
    GmbDisconnectRequest,
    GmbProfileStatusOut,
    ReviewApproveRequest,
    ReviewDraftResponse,
    ReviewGenerateRequest,
    ReviewOut,
    ReviewRejectRequest,
    ReviewResponseOut,
)
from app.services import gmb_service, response_service, review_service
from app.services.response_service import AI_REPLY_FEATURE

router = APIRouter(prefix="/reviews", tags=["reviews"])
gmb_router = APIRouter(prefix="/gmb", tags=["gmb"])


@router.get("", response_model=list[ReviewOut], status_code=status.HTTP_200_OK)
async def list_reviews(
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> list[ReviewOut]:
    """Manager+ — same rank as GET /branches and GET /loyalty/analytics.
    The reviews themselves need no approval (Doc 5: a diner posts straight
    to Google); only each review's AI-drafted *reply* carries an
    approval_state, nested in `response`."""
    return await response_service.list_reviews(session)


@router.post("/generate", response_model=ReviewDraftResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def generate_review(
    request: Request,
    payload: ReviewGenerateRequest,
    session: AsyncSession = Depends(get_db),
    current_customer: Customer | None = Depends(get_current_customer_optional),
) -> ReviewDraftResponse:
    """Draft a review from a rating and tags. Public — the diner has no session.

    Unauthenticated by design (REVIEW-01: "No auth required"), so the QR token
    is the only credential and this rate limit is the only brake on someone
    burning a tenant's AI budget. Both matter: a leaked QR token is a bill.

    `get_current_customer_optional` never raises — a diner with no loyalty
    session still gets their draft exactly as before. When one does exist,
    review_service records it to that customer's own history (profile page's
    "reviews you've sent"); see review_service.generate_review_draft.
    """
    return await review_service.generate_review_draft(payload, session, current_customer)


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
    _plan_ok: User = Depends(check_subscription_tier(AI_REPLY_FEATURE)),
    session: AsyncSession = Depends(get_db),
) -> ReviewResponseOut:
    """Approve an AI-drafted response. Manager+ only — Staff gets 403 (REVIEW-02).
    402s if the tenant's plan doesn't include AI review replies."""
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
    _plan_ok: User = Depends(check_subscription_tier(AI_REPLY_FEATURE)),
    session: AsyncSession = Depends(get_db),
) -> ReviewResponseOut:
    """Reject a draft, triggering regeneration with a variation prompt (REVIEW-02).
    402s if the tenant's plan doesn't include AI review replies."""
    return await response_service.reject_response(session, review_response_id, payload.reason)


# --- GMB OAuth: connect / callback / disconnect (REVIEW-03) -----------------


async def _get_owned_branch(session: AsyncSession, branch_id: uuid.UUID) -> Branch:
    """404s a branch id that doesn't exist, or belongs to another tenant.

    No explicit tenant_id filter needed: `require_role` has already bound
    `app.tenant_id` via `get_current_user`, so RLS makes a cross-tenant
    branch id invisible to this query rather than something to check for.
    """
    result = await session.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "BRANCH_NOT_FOUND", "message": "No such branch."}},
        )
    return branch


@gmb_router.get("/connect", response_model=GmbConnectResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def connect_gmb(
    request: Request,
    branch_id: uuid.UUID,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> GmbConnectResponse:
    """Start the GMB OAuth flow for one branch. Owner+ only (REVIEW-03)."""
    await _get_owned_branch(session, branch_id)
    state = await gmb_service.create_oauth_state(current_user.tenant_id, branch_id)
    return GmbConnectResponse(authorize_url=gmb_service.build_authorize_url(state))


@gmb_router.get("/oauth/callback", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
@limiter.limit("10/minute")
async def gmb_oauth_callback(
    request: Request,
    code: str,
    state: str,
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Google's OAuth redirect target — no bearer token on a top-level
    browser redirect, so the single-use `state` minted by /gmb/connect for
    an already-authenticated Owner is what authorises this request instead.

    This is a top-level browser navigation, never an XHR the dashboard reads
    directly — returning JSON here would strand the owner's tab on a bare API
    body instead of back on the page they started from. Every exit path
    below redirects to the Google Profile Link page instead of raising, with
    the outcome carried in a query param the page reads to show a toast.
    """
    from app.db import rls  # noqa: PLC0415 — only this route needs it

    dest = "/dashboard/google-profile"

    parsed = await gmb_service.consume_oauth_state(state)
    if parsed is None:
        return RedirectResponse(url=f"{dest}?gmb=expired", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    tenant_id = uuid.UUID(parsed["tenant_id"])
    branch_id = uuid.UUID(parsed["branch_id"])
    await rls.set_tenant_context(session, tenant_id)

    try:
        await gmb_service.complete_connect(
            session, code=code, tenant_id=tenant_id, branch_id=branch_id
        )
    except gmb_service.GMBOAuthError:
        return RedirectResponse(url=f"{dest}?gmb=failed", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    return RedirectResponse(url=f"{dest}?gmb=connected", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@gmb_router.post("/disconnect", response_model=GmbProfileStatusOut, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def disconnect_gmb(
    request: Request,
    payload: GmbDisconnectRequest,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> GmbProfileStatusOut:
    """Revoke and disconnect a branch's GMB profile. Owner+ only (REVIEW-03)."""
    await _get_owned_branch(session, payload.branch_id)
    profile = await gmb_service.disconnect(session, current_user.tenant_id, payload.branch_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "GMB_NOT_CONNECTED",
                    "message": "This branch has no connected Google Business Profile.",
                }
            },
        )
    return GmbProfileStatusOut.model_validate(profile)
