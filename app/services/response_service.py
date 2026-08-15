"""QuickBite — Review response drafting + approval workflow (REVIEW-02).

Owns the `review_responses` lifecycle: pending -> approved -> posted, or
pending -> rejected -> pending (regenerated). Posting to GMB itself is a
Celery task (`app.workers.tasks.post_approved_response`) so an approval
request returns immediately — the acceptance criterion is "posted within 5
minutes", not "posted before the HTTP response".

RLS note: by the time a route reaches here, `get_current_user` /
`require_role` has already bound `app.tenant_id` on the session (see
`resolve_principal` in `app/api/v1/dependencies/auth.py`), so every query
below is tenant-scoped by Postgres itself, not by an extra WHERE clause.
"""

import uuid

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import broadcast, cache_service
from app.db import rls
from app.db.models.reputation import CustomerReview, GMBProfile, ReviewResponse
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.services import ai_engine, gmb_service
from app.services.ai_engine import AIProvidersUnavailable
from app.services.gmb_service import GMBNotConnected, GMBTokenExpired

# TTL for the "already posted" Redis flag in post_approved_response_to_gmb —
# long enough to outlive Celery's max_retries=3 backoff window many times over.
_GMB_POST_IDEMPOTENCY_TTL_SECONDS = 86400

logger = structlog.get_logger(__name__)

# Hourly batch caps how many un-answered reviews it drafts in one run, so a
# sync backlog cannot turn the beat task into an unbounded, budget-draining run.
BATCH_SIZE = 50


async def _get_response_for_update(session: AsyncSession, review_response_id: uuid.UUID) -> ReviewResponse:
    result = await session.execute(
        select(ReviewResponse).where(ReviewResponse.id == review_response_id)
    )
    response = result.scalar_one_or_none()
    if response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "REVIEW_RESPONSE_NOT_FOUND",
                    "message": "No such review response.",
                }
            },
        )
    return response


def _require_pending(response: ReviewResponse) -> None:
    if response.approval_state != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "REVIEW_RESPONSE_NOT_PENDING",
                    "message": f"This response is already {response.approval_state}.",
                }
            },
        )


async def approve_response(
    session: AsyncSession, review_response_id: uuid.UUID, current_user: User, final_text: str | None
) -> ReviewResponse:
    """Approve a draft. Queues the GMB post — does not post synchronously."""
    from app.workers.tasks import post_approved_response  # noqa: PLC0415 — avoids a task/service import cycle

    response = await _get_response_for_update(session, review_response_id)
    _require_pending(response)

    response.final_text = final_text or response.ai_draft
    response.approval_state = "approved"
    response.approved_by_user_id = current_user.id
    await session.commit()

    post_approved_response.delay(str(response.id))

    # DASH-01: nudges an open dashboard tab to re-poll /dashboard/stats so
    # pending_approvals drops without the owner having to refresh.
    await broadcast.publish_event(
        response.tenant_id, "review_approved", {"review_response_id": str(response.id)}
    )

    logger.info(
        "review_response.approved",
        review_response_id=str(response.id),
        tenant_id=str(response.tenant_id),
        approved_by=str(current_user.id),
    )
    return response


async def reject_response(
    session: AsyncSession, review_response_id: uuid.UUID, reason: str | None
) -> ReviewResponse:
    """Reject a draft and regenerate a variation in its place, still pending."""
    response = await _get_response_for_update(session, review_response_id)
    _require_pending(response)

    review_result = await session.execute(
        select(CustomerReview).where(CustomerReview.id == response.review_id)
    )
    review = review_result.scalar_one()
    restaurant_name = await _get_restaurant_name(session, response.tenant_id)

    try:
        draft = await ai_engine.generate_response_draft(
            review_id=str(review.id),
            restaurant_name=restaurant_name,
            rating=review.rating,
            review_body=review.review_body or "",
            variation=True,
        )
    except AIProvidersUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "AI_PROVIDER_UNAVAILABLE",
                    "message": "We couldn't regenerate a response just now. Please try again.",
                }
            },
            headers={"Retry-After": "30"},
        ) from exc

    response.ai_draft = draft.text
    response.ai_model_used = draft.model
    response.final_text = None
    response.approval_state = "pending"
    await session.commit()

    logger.info(
        "review_response.rejected",
        review_response_id=str(response.id),
        tenant_id=str(response.tenant_id),
        reason_given=reason is not None,
    )
    return response


async def generate_pending_response_drafts(session: AsyncSession) -> int:
    """Draft an AI response for every synced review that doesn't have one yet.

    Runs hourly via Celery beat (REVIEW-02's "within 1 hour of sync"
    criterion), with no authenticated principal to bind tenant context — so
    this walks tenants explicitly via `rls.tenant_context`, the same pattern
    the Razorpay webhook and outbox drain use. `restaurant.tenants` itself
    carries no RLS policy (it is the root of tenancy, same reasoning as the
    anonymous QR-token lookups in `review_service`), so listing tenant ids
    needs no context of its own.

    `idempotency_key` is deterministic per review (`review-reply:{review_id}`),
    so `ON CONFLICT DO NOTHING` makes a rerun over the same backlog a no-op
    rather than a duplicate draft.
    """
    tenant_ids = (await session.execute(select(Tenant.id))).scalars().all()

    drafted = 0
    for tenant_id in tenant_ids:
        if drafted >= BATCH_SIZE:
            break
        async with rls.tenant_context(session, tenant_id):
            drafted += await _draft_for_tenant(session, tenant_id, limit=BATCH_SIZE - drafted)

    logger.info("review_response.batch_drafted", count=drafted)
    return drafted


async def _draft_for_tenant(session: AsyncSession, tenant_id: uuid.UUID, *, limit: int) -> int:
    result = await session.execute(
        select(CustomerReview)
        .outerjoin(ReviewResponse, ReviewResponse.review_id == CustomerReview.id)
        .where(CustomerReview.source == "google", ReviewResponse.id.is_(None))
        .limit(limit)
    )
    reviews = result.scalars().all()
    if not reviews:
        return 0

    restaurant_name = await _get_restaurant_name(session, tenant_id)
    drafted = 0
    for review in reviews:
        try:
            draft = await ai_engine.generate_response_draft(
                review_id=str(review.id),
                restaurant_name=restaurant_name,
                rating=review.rating,
                review_body=review.review_body or "",
            )
        except AIProvidersUnavailable:
            logger.warning("review_response.batch_draft_failed", review_id=str(review.id))
            continue

        statement = (
            insert(ReviewResponse)
            .values(
                id=uuid.uuid4(),
                review_id=review.id,
                tenant_id=tenant_id,
                ai_draft=draft.text,
                approval_state="pending",
                ai_model_used=draft.model,
                idempotency_key=f"review-reply:{review.id}",
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        await session.execute(statement)
        drafted += 1

    await session.commit()
    return drafted


async def post_approved_response_to_gmb(session: AsyncSession, review_response_id: uuid.UUID) -> str:
    """Post one approved response to GMB. Called by the `post_approved_response`
    Celery task — kept here, not in the task, so it is unit-testable directly.

    Returns an outcome string rather than raising, since every non-"posted"
    case (already sent, not approved, no GMB profile, token issues) is an
    expected skip the task should log and move past, not a Celery retry.

    Idempotency (REVIEW-02: "prevents duplicate GMB posts on Celery retry")
    is two layers: a Redis flag keyed on the row id short-circuits a retry
    cheaply, and `approval_state` on the row is the authoritative guard once
    Postgres is touched — the Redis flag is set only after `approval_state`
    flips to "posted", so a crash between the two just means the next retry
    finds the row already posted and returns early there instead.
    """
    posted_key = f"gmb_reply_posted:{review_response_id}"
    if await cache_service.exists(posted_key):
        logger.info("review_response.post_skipped_already_sent", review_response_id=str(review_response_id))
        return "already_sent"

    response = (
        await session.execute(select(ReviewResponse).where(ReviewResponse.id == review_response_id))
    ).scalar_one_or_none()
    if response is None or response.approval_state != "approved":
        logger.warning("review_response.post_skipped_not_approved", review_response_id=str(review_response_id))
        return "not_approved"

    async with rls.tenant_context(session, response.tenant_id):
        review = (
            await session.execute(select(CustomerReview).where(CustomerReview.id == response.review_id))
        ).scalar_one()
        profile = (
            await session.execute(select(GMBProfile).where(GMBProfile.branch_id == review.branch_id))
        ).scalar_one_or_none()

        if profile is None or review.external_review_id is None:
            logger.warning(
                "review_response.post_skipped_no_gmb_profile", review_response_id=str(review_response_id)
            )
            return "no_gmb_profile"

        try:
            await gmb_service.post_review_reply(
                profile, review.external_review_id, response.final_text or response.ai_draft
            )
        except (GMBNotConnected, GMBTokenExpired) as exc:
            logger.warning(
                "review_response.post_skipped",
                review_response_id=str(review_response_id),
                reason=type(exc).__name__,
            )
            return "not_connected" if isinstance(exc, GMBNotConnected) else "token_expired"

        response.approval_state = "posted"
        await session.commit()

    await cache_service.set(posted_key, "1", ttl=_GMB_POST_IDEMPOTENCY_TTL_SECONDS)
    logger.info("review_response.posted_to_gmb", review_response_id=str(review_response_id))
    return "posted"


async def _get_restaurant_name(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    result = await session.execute(select(Tenant.name).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none() or "the restaurant"
