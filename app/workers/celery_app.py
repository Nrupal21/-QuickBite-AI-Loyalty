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
)

celery_app.autodiscover_tasks(["app.workers"])
