"""QuickBite — Celery tasks: sync_gmb, batch_ai, send_rewards, reset_usage, cleanup.

Feature tasks arrive with their tickets (REVIEW-03, SUB-02, NICE-02...).
`ping` exists so INFRA-03's "worker connects to Redis broker" criterion is
verifiable: `celery -A app.workers.celery_app call app.workers.tasks.ping`.

`drain_projection_outbox` is the payment-projection worker (see
projection_service.py): it owns no business logic itself, only the
task-scheduling wrapper around one drain call.

`batch_generate_ai_responses` and `post_approved_response` are REVIEW-02:
the hourly draft sweep and the per-approval GMB post, both thin wrappers
around `response_service` for the same reason `drain_projection_outbox` is
thin around `projection_service`.
"""

import asyncio

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(bind=True, max_retries=3)
def ping(self) -> str:
    """Broker connectivity check — returns 'pong'."""
    logger.info("celery.ping")
    return "pong"


@celery_app.task(bind=True, max_retries=3)
def drain_projection_outbox(self) -> int:
    """Send due payment.projection_outbox rows to Firestore.

    Scheduled every few seconds via Celery beat. Each run opens its own DB
    session and commits per-row inside projection_service.drain_pending — a
    task failure here (e.g. Firestore unreachable) leaves rows `pending` for
    the next run rather than losing them, since nothing here is retried at
    the Celery-task level; retries happen at the row level instead.
    """
    from app.db.base import async_session_factory  # noqa: PLC0415
    from app.services import projection_service  # noqa: PLC0415

    async def _run() -> int:
        async with async_session_factory() as session:
            return await projection_service.drain_pending(session)

    sent = asyncio.run(_run())
    logger.info("billing.projection.drained", sent=sent)
    return sent


@celery_app.task(bind=True, max_retries=3)
def batch_generate_ai_responses(self) -> int:
    """Hourly sweep: draft an AI response for every review that lacks one.

    Scheduled via `celery_app.conf.beat_schedule` (REVIEW-02: "AI draft
    generated for all new GMB reviews within 1 hour of sync").
    """
    from app.db.base import async_session_factory  # noqa: PLC0415
    from app.services import response_service  # noqa: PLC0415

    async def _run() -> int:
        async with async_session_factory() as session:
            return await response_service.generate_pending_response_drafts(session)

    drafted = asyncio.run(_run())
    logger.info("review_response.batch_task_done", drafted=drafted)
    return drafted


@celery_app.task(bind=True, max_retries=3)
def post_approved_response(self, review_response_id: str) -> None:
    """Post one approved response to GMB. Triggered by the approve route.

    Idempotency guard is Redis-backed and keyed on `review_responses.
    idempotency_key`, not the row id, per REVIEW-02: "idempotency key
    prevents duplicate GMB posts on Celery retry". The row's approval_state
    is the second, authoritative guard — the Redis key only short-circuits
    the common case cheaply before touching Postgres or the GMB API.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.core import cache_service  # noqa: PLC0415
    from app.db.base import async_session_factory  # noqa: PLC0415
    from app.db.models.reputation import CustomerReview, GMBProfile, ReviewResponse  # noqa: PLC0415
    from app.db import rls  # noqa: PLC0415
    from app.services import gmb_service  # noqa: PLC0415
    from app.services.gmb_service import GMBNotConnected, GMBTokenExpired  # noqa: PLC0415

    posted_key = f"gmb_reply_posted:{review_response_id}"

    async def _run() -> None:
        if await cache_service.exists(posted_key):
            logger.info("review_response.post_skipped_already_sent", review_response_id=review_response_id)
            return

        async with async_session_factory() as session:
            response = (
                await session.execute(select(ReviewResponse).where(ReviewResponse.id == review_response_id))
            ).scalar_one_or_none()
            if response is None or response.approval_state != "approved":
                logger.warning(
                    "review_response.post_skipped_not_approved",
                    review_response_id=review_response_id,
                )
                return

            async with rls.tenant_context(session, response.tenant_id):
                review = (
                    await session.execute(select(CustomerReview).where(CustomerReview.id == response.review_id))
                ).scalar_one()
                profile = (
                    await session.execute(
                        select(GMBProfile).where(GMBProfile.branch_id == review.branch_id)
                    )
                ).scalar_one_or_none()

                if profile is None or review.external_review_id is None:
                    logger.warning(
                        "review_response.post_skipped_no_gmb_profile",
                        review_response_id=review_response_id,
                    )
                    return

                try:
                    await gmb_service.post_review_reply(
                        profile, review.external_review_id, response.final_text or response.ai_draft
                    )
                except (GMBNotConnected, GMBTokenExpired) as exc:
                    logger.warning(
                        "review_response.post_skipped",
                        review_response_id=review_response_id,
                        reason=type(exc).__name__,
                    )
                    return

                response.approval_state = "posted"
                await session.commit()

        await cache_service.set(posted_key, "1", ttl=86400)

    asyncio.run(_run())
