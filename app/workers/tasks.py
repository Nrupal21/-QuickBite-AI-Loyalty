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


async def _finalize_subscription_cancellation_async(
    session, subscription_id: str, tenant_id: str
) -> None:
    """Async helper for finalize_subscription_cancellation Celery task.

    Checks cancel_at_period_end before calling Razorpay, so a reactivation
    in the meantime makes this a safe no-op (idempotent under retry). Runs
    inside `rls.tenant_context` — a fresh worker session has no tenant bound,
    and `payment.subscriptions` is FORCE ROW LEVEL SECURITY: an unscoped
    query here returns zero rows (not an error), which would silently no-op
    every cancellation forever. Same pattern `review_sync_service.sync_tenant`
    uses for the same reason (a worker session that starts with no principal).
    """
    import uuid  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from app.db import rls  # noqa: PLC0415
    from app.db.models.outbox import ProjectionOutbox  # noqa: PLC0415
    from app.db.models.subscription import Subscription  # noqa: PLC0415

    async with rls.tenant_context(session, uuid.UUID(tenant_id)):
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

        # cancel_at_cycle_end=0: this task fires AT current_period_end, so the
        # cycle we were waiting out is already over — telling Razorpay to
        # cancel "at cycle end" here would target the *next* cycle and charge
        # once more. 0 cancels immediately, which is what "now" means at this
        # point in time.
        client.subscription.cancel(
            subscription.provider_subscription_ref, data={"cancel_at_cycle_end": 0}
        )
        # Close the loop locally rather than waiting on a webhook that might
        # never arrive — the webhook remains the authoritative reconciler for
        # everything else (status transitions, period rollovers), but the one
        # fact this task itself just caused is safe to record directly.
        # billing_service._enqueue_projection's own docstring states the
        # outbox pattern's whole correctness argument: either both this row
        # and the Subscription update commit, or neither does — so this row
        # is added in the same transaction as the status write below, not
        # bolted on afterward.
        subscription.status = "canceled"
        session.add(
            ProjectionOutbox(
                tenant_id=uuid.UUID(tenant_id),
                aggregate_type="subscription",
                aggregate_id=subscription.id,
                version=int(datetime.now(UTC).timestamp()),
                event_type="subscription.finalized_cancellation",
                payload={
                    "status": subscription.status,
                    "provider": subscription.provider,
                    "current_period_end": subscription.current_period_end.isoformat()
                    if subscription.current_period_end
                    else None,
                    "cancel_at_period_end": subscription.cancel_at_period_end,
                },
            )
        )
        await session.commit()
        logger.info(
            "billing.subscription.finalized", subscription_id=subscription_id
        )


@celery_app.task(bind=True, max_retries=3, default_retry_delay=300)
def finalize_subscription_cancellation(self, subscription_id: str, tenant_id: str) -> None:
    """Finalize subscription cancellation at the billing period end.

    Doc 3 / this session's billing spec: the real Razorpay cancel call is
    deferred to here, scheduled for the subscription's current_period_end,
    because Razorpay has no API to reverse a sent cancellation. Re-checks
    cancel_at_period_end before acting so a reactivation in the meantime
    makes this a safe no-op. Retries on any failure (network blip, Razorpay
    5xx) rather than losing the cancellation silently — the local flag stays
    True either way, so a retry is always safe to attempt again."""
    from app.db.base import async_session_factory  # noqa: PLC0415

    async def _run() -> None:
        async with async_session_factory() as session:
            await _finalize_subscription_cancellation_async(session, subscription_id, tenant_id)

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — retried, not swallowed
        logger.warning(
            "billing.subscription.finalize_failed",
            subscription_id=subscription_id,
            error=str(exc),
        )
        raise self.retry(exc=exc) from exc
