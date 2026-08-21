"""QuickBite — Review composer domain logic (REVIEW-01).

Sits between the public route and `ai_engine`: resolves the QR token to a
branch, binds tenant context, sanitises the tags, and records usage. The AI
orchestration itself (cache, provider order, fallback) lives in ai_engine so
this module stays about *the review*, not about models.

Two things worth knowing about the tenant boundary here.

The endpoint is public — the diner just scanned a QR code and has no session —
so `tenant_id` cannot come from a token. It is derived from the branch that
owns the QR token, and only then bound for RLS. That ordering is forced:
nothing can be scoped to a tenant we have not identified yet.

`restaurant.branches` is itself RLS-protected (migration 0001), so the branch
lookup necessarily runs *before* any tenant context exists. That is the same
shape as the anonymous loyalty scan in `LoyaltyService._get_active_branch`,
and it works because the QR token is a high-entropy unguessable secret doing
the job the tenant filter would otherwise do.
"""

import uuid

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import prompt_guard
from app.db import bootstrap, rls
from app.db.models.branch import Branch
from app.db.models.customer import Customer, ReviewDraft
from app.db.models.tenant import Tenant
from app.schemas.reputation import ReviewDraftResponse, ReviewGenerateRequest
from app.services import ai_engine, usage_service
from app.services.ai_engine import AIProvidersUnavailable

logger = structlog.get_logger(__name__)

# Preview only — draft_excerpt truncates here, matching the DB column width
# (migration 0013). The full draft is customer-authored free text the diner
# may heavily edit or never post; there is no reason to retain all of it.
_DRAFT_EXCERPT_LENGTH = 280


async def generate_review_draft(
    request: ReviewGenerateRequest, session: AsyncSession, customer: Customer | None = None
) -> ReviewDraftResponse:
    branch = await _get_active_branch(session, request.branch_qr_token)
    tags = _sanitise_tags(request.tags, branch.tenant_id)
    restaurant_name = await _get_restaurant_name(session, branch.tenant_id)

    try:
        draft = await ai_engine.generate_draft(
            branch_id=str(branch.id),
            restaurant_name=restaurant_name,
            rating=request.rating,
            tags=tags,
        )
    except AIProvidersUnavailable as exc:
        # 503 and no usage increment: the tenant is not billed for our outage.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "AI_PROVIDER_UNAVAILABLE",
                    "message": "We couldn't write your review just now. Please try again.",
                }
            },
            headers={"Retry-After": "30"},
        ) from exc

    # Only a real generation costs money, so a cache hit must not be billed
    # twice for the same draft.
    needs_commit = not draft.cached
    if needs_commit:
        await usage_service.increment_ai_usage(session, branch.tenant_id)

    # A logged-in diner's own draft history for their profile page (migration
    # 0013). Most scans are anonymous by design (REVIEW-01) — customer is only
    # non-None when a session already existed, and only recorded when it
    # belongs to *this* branch's tenant: a Customer row is siloed per tenant
    # (one loyalty membership per restaurant), so a session from a different
    # restaurant has nothing meaningful to attribute here.
    if customer is not None and customer.tenant_id == branch.tenant_id:
        session.add(
            ReviewDraft(
                id=uuid.uuid4(),
                tenant_id=branch.tenant_id,
                branch_id=branch.id,
                customer_id=customer.id,
                rating=request.rating,
                tags=tags,
                draft_excerpt=draft.text[:_DRAFT_EXCERPT_LENGTH],
            )
        )
        needs_commit = True

    if needs_commit:
        await session.commit()

    logger.info(
        "review.draft.generated",
        branch_id=str(branch.id),
        tenant_id=str(branch.tenant_id),
        rating=request.rating,
        tag_count=len(tags),
        model=draft.model,
        cached=draft.cached,
        customer_attributed=customer is not None,
    )
    return ReviewDraftResponse(
        draft=draft.text, model=draft.model, cached=draft.cached, tags=tags
    )


def _sanitise_tags(tags: list[str], tenant_id: uuid.UUID) -> list[str]:
    """Strip injection structure, and log any attempt without failing the request.

    A diner who pastes something odd still gets their review — refusing would
    teach an attacker exactly which inputs trip the filter, and would punish
    the far more common case of someone typing something harmless but strange.
    """
    for tag in tags:
        if prompt_guard.contains_injection_attempt(tag):
            # The tag itself is never logged: it is customer-authored free text.
            logger.warning(
                "review.draft.injection_attempt_blocked", tenant_id=str(tenant_id)
            )

    sanitised = prompt_guard.sanitise_tags(tags)
    if not sanitised:
        # Everything the diner sent was stripped, so there is nothing to write
        # a review about. Same message as the empty-list case.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "TAGS_REQUIRED",
                    "message": "Please select at least 1 tag",
                }
            },
        )
    return sanitised


async def _get_active_branch(session: AsyncSession, qr_token: str) -> Branch:
    """Resolve the QR token to a tenant, bind it, then read the branch scoped.

    `restaurant.branches` is RLS-protected, so this is the endpoint's
    chicken-and-egg: the request is unauthenticated and the token is the only
    thing that knows which tenant it belongs to. Binding happens here rather
    than in the caller so both unauthenticated entry points — this and the
    loyalty scan — get it for free instead of each remembering to.
    """
    tenant_id = await bootstrap.tenant_for_branch_qr_token(session, qr_token)
    if tenant_id is not None:
        await rls.set_tenant_context(session, tenant_id)

    result = await session.execute(
        select(Branch).where(Branch.qr_code_token == qr_token, Branch.is_active.is_(True))
    )
    branch = result.scalar_one_or_none()
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "LOYALTY_INVALID_QR",
                    "message": "This QR code is no longer active.",
                }
            },
        )
    return branch


async def _get_restaurant_name(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    """The one piece of restaurant context the prompt needs.

    Falls back to a generic noun rather than failing: a missing tenant row
    should not cost the diner their review, and the name is flavour in the
    prompt, not correctness.
    """
    result = await session.execute(select(Tenant.name).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none() or "the restaurant"
