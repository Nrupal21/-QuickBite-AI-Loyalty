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

`sync_gmb_tenant` is ADMIN-01: triggered by the Super Admin panel's
`POST /admin/tenants/{id}/sync-gmb`. `sync_all_gmb_profiles` is REVIEW-03's
own periodic sweep, scheduled via Celery beat — both are thin wrappers
around `review_sync_service`, same shape as `drain_projection_outbox`
around `projection_service`.
"""

import asyncio
from typing import Any

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
def sync_gmb_tenant(self, tenant_id: str) -> int:
    """Sync every connected GMB profile for one tenant. Triggered by
    `POST /admin/tenants/{id}/sync-gmb` (ADMIN-01).

    Runs its own session scoped to the one tenant it was given, the same
    pattern `drain_projection_outbox` uses — ADMIN-01's endpoint never needs
    cross-tenant BYPASSRLS for this task. The actual pull lives in
    `review_sync_service.sync_tenant`.
    """
    import uuid  # noqa: PLC0415

    from app.db.base import async_session_factory  # noqa: PLC0415
    from app.services import review_sync_service  # noqa: PLC0415

    async def _run() -> int:
        async with async_session_factory() as session:
            return await review_sync_service.sync_tenant(session, uuid.UUID(tenant_id))

    stored = asyncio.run(_run())
    logger.info("admin.gmb_sync.done", tenant_id=tenant_id, reviews_stored=stored)
    return stored


@celery_app.task(bind=True, max_retries=3)
def sync_all_gmb_profiles(self) -> int:
    """Periodic sweep: pull new reviews for every connected GMB profile,
    across every tenant. Scheduled via `celery_app.conf.beat_schedule`
    (REVIEW-03) — this is what keeps `sync_gmb_tenant` from being the only
    way reviews ever arrive; that one stays admin/manual-trigger only.
    """
    from app.db.base import async_session_factory  # noqa: PLC0415
    from app.services import review_sync_service  # noqa: PLC0415

    async def _run() -> int:
        async with async_session_factory() as session:
            return await review_sync_service.sync_all_connected(session)

    stored = asyncio.run(_run())
    logger.info("gmb.sync.sweep_task_done", reviews_stored=stored)
    return stored


@celery_app.task(bind=True, max_retries=3)
def post_approved_response(self, review_response_id: str) -> str:
    """Post one approved response to GMB. Triggered by the approve route.

    Idempotency (REVIEW-02: "prevents duplicate GMB posts on Celery retry")
    and the actual posting logic live in `response_service.
    post_approved_response_to_gmb` — this stays a thin wrapper, same as
    `drain_projection_outbox` around `projection_service`.
    """
    import uuid  # noqa: PLC0415

    from app.db.base import async_session_factory  # noqa: PLC0415
    from app.services import response_service  # noqa: PLC0415

    async def _run() -> str:
        async with async_session_factory() as session:
            return await response_service.post_approved_response_to_gmb(
                session, uuid.UUID(review_response_id)
            )

    outcome = asyncio.run(_run())
    logger.info("review_response.post_task_done", review_response_id=review_response_id, outcome=outcome)
    return outcome


# --- Billing cancellation ---------------------------------------------------

_razorpay_client: Any = None


def _get_razorpay_client() -> Any:
    """Lazy Razorpay SDK client — mirrors billing_service._get_client.

    Not constructed at import: settings default RAZORPAY_KEY_ID/SECRET to
    empty, so an unconfigured environment (every test run) must never attempt
    to build a client.
    """
    global _razorpay_client  # noqa: PLW0603
    if _razorpay_client is not None:
        return _razorpay_client
    from app.core.config import settings  # noqa: PLC0415

    if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
        return None
    import razorpay  # noqa: PLC0415

    _razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    return _razorpay_client


async def _finalize_subscription_cancellation_async(session, subscription_id: str) -> None:
    """Async helper for finalize_subscription_cancellation Celery task.

    Checks cancel_at_period_end before calling Razorpay, so a reactivation
    in the meantime makes this a safe no-op (idempotent under retry).
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.db.models.subscription import Subscription  # noqa: PLC0415

    result = await session.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()
    if subscription is None or not subscription.cancel_at_period_end:
        # Deleted, or reactivated before this task fired — no-op, matches
        # the idempotent-task convention every other task here follows.
        logger.info(
            "billing.subscription.finalize_skipped", subscription_id=subscription_id
        )
        return

    client = _get_razorpay_client()
    if client is None:
        logger.warning(
            "billing.subscription.finalize_no_client", subscription_id=subscription_id
        )
        return

    client.subscription.cancel(
        subscription.provider_subscription_ref, data={"cancel_at_cycle_end": 1}
    )
    logger.info(
        "billing.subscription.finalized", subscription_id=subscription_id
    )


@celery_app.task(bind=True, max_retries=3)
def finalize_subscription_cancellation(self, subscription_id: str) -> None:
    """Finalize subscription cancellation at the billing period end.

    Doc 3 / this session's billing spec: the real Razorpay cancel call is
    deferred to here, scheduled for the subscription's current_period_end,
    because Razorpay has no API to reverse a sent cancellation. Re-checks
    cancel_at_period_end before acting so a reactivation in the meantime
    makes this a safe no-op."""
    from app.db.base import async_session_factory  # noqa: PLC0415

    async def _run() -> None:
        async with async_session_factory() as session:
            await _finalize_subscription_cancellation_async(session, subscription_id)

    asyncio.run(_run())
