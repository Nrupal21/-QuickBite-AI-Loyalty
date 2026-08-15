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
`POST /admin/tenants/{id}/sync-gmb`. It only stamps `last_synced_at` today —
the real inbound review pull is REVIEW-03 and does not exist yet.
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
def sync_gmb_tenant(self, tenant_id: str) -> int:
    """Sync every connected GMB profile for one tenant. Triggered by
    `POST /admin/tenants/{id}/sync-gmb` (ADMIN-01).

    This only stamps `last_synced_at` — pulling reviews from the Google My
    Business API is REVIEW-03 scope and does not exist yet
    (TODO(REVIEW-03): replace the stamp below with an actual GMB pull).
    Runs its own session scoped to the one tenant it was given via
    `rls.tenant_context`, the same pattern `drain_projection_outbox` uses —
    ADMIN-01's endpoint never needs cross-tenant BYPASSRLS for this task.
    """
    import uuid  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from app.db import rls  # noqa: PLC0415
    from app.db.base import async_session_factory  # noqa: PLC0415
    from app.db.models.reputation import GMBProfile  # noqa: PLC0415

    async def _run() -> int:
        tid = uuid.UUID(tenant_id)
        async with async_session_factory() as session, rls.tenant_context(session, tid):
            result = await session.execute(
                select(GMBProfile).where(
                    GMBProfile.tenant_id == tid, GMBProfile.is_connected.is_(True)
                )
            )
            profiles = result.scalars().all()
            for profile in profiles:
                profile.last_synced_at = datetime.now(UTC)
            await session.commit()
            return len(profiles)

    synced = asyncio.run(_run())
    logger.info("admin.gmb_sync.done", tenant_id=tenant_id, profiles_synced=synced)
    return synced


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
