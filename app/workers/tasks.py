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

`send_campaign_task` and `process_whatsapp_webhook_event` are the WhatsApp
marketing fan-out and webhook-event workers, thin wrappers around
`campaign_service` the same way. Idempotency for the campaign send is a DB
row (CampaignRecipient's unique constraint), not a Celery-level retry guard —
same choice `post_approved_response` makes for the same reason.
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


@celery_app.task(bind=True, max_retries=3)
def send_campaign_task(self, campaign_id: str) -> int:
    """Send every still-queued recipient of one campaign. Triggered by
    `campaign_service.launch_campaign`. If Meta's rate limit is hit,
    `send_campaign_batch` stops early (leaving the rest `queued`) rather than
    failing them, and this task retries after a delay so the remainder goes
    out once the tenant's messaging tier window resets.
    """
    import uuid  # noqa: PLC0415

    from app.db.base import async_session_factory  # noqa: PLC0415
    from app.services import campaign_service  # noqa: PLC0415

    async def _run() -> int:
        async with async_session_factory() as session:
            return await campaign_service.send_campaign_batch(session, uuid.UUID(campaign_id))

    sent = asyncio.run(_run())
    logger.info("marketing.campaign.task_done", campaign_id=campaign_id, sent=sent)
    return sent


@celery_app.task(bind=True, max_retries=3)
def process_whatsapp_webhook_event(self, payload: dict) -> None:
    """Process one Meta WhatsApp webhook envelope off the request path, so
    `POST /webhooks/whatsapp` can ack Meta immediately — Meta disables a
    webhook subscription that doesn't respond fast, so status/opt-out
    processing must never block the HTTP response.
    """
    from app.db.base import async_session_factory  # noqa: PLC0415
    from app.services import campaign_service  # noqa: PLC0415

    async def _run() -> None:
        async with async_session_factory() as session:
            await campaign_service.process_webhook_payload(session, payload)

    asyncio.run(_run())
    logger.info("marketing.webhook.task_done")
