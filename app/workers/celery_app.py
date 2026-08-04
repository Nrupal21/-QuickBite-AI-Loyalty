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
    },
)

celery_app.autodiscover_tasks(["app.workers"])
