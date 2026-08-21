"""QuickBite — Celery application configuration (INFRA-03).

Uses Redis as broker and result backend. All tasks use bind=True +
max_retries=3 and accept only JSON-serializable arguments (UUIDs as strings).
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery("quickbite", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    # REVIEW-02: "AI draft generated for all new GMB reviews within 1 hour of
    # sync" — hourly is the criterion's own unit, not a tuned interval.
    beat_schedule={
        "review-02-batch-generate-ai-responses": {
            "task": "app.workers.tasks.batch_generate_ai_responses",
            "schedule": 3600.0,
        },
        # drain_projection_outbox's own docstring has always described this as
        # "scheduled every few seconds via Celery beat" — it never actually
        # was. Without this entry, billing_service correctly writes
        # payment.projection_outbox rows in the same transaction as every
        # ledger change, and projection_service correctly knows how to drain
        # them, but nothing ever calls drain — the Firestore mirror silently
        # never updates. 10s matches "a few seconds" without hammering
        # Firestore on an empty queue between real billing events.
        "billing-drain-projection-outbox": {
            "task": "app.workers.tasks.drain_projection_outbox",
            "schedule": 10.0,
        },
        # REVIEW-03: the only path new Google reviews reach QuickBite without
        # an Owner manually clicking "sync" in the admin panel. 30 minutes
        # keeps REVIEW-02's "within 1 hour of sync" draft criterion reachable
        # even for a review that lands right after a sweep just ran.
        "review-03-sync-gmb-profiles": {
            "task": "app.workers.tasks.sync_all_gmb_profiles",
            "schedule": 1800.0,
        },
    },
)

celery_app.autodiscover_tasks(["app.workers"])
