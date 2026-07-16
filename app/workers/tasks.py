"""QuickBite — Celery tasks: sync_gmb, batch_ai, send_rewards, reset_usage, cleanup.

Feature tasks arrive with their tickets (REVIEW-03, SUB-02, NICE-02...).
`ping` exists so INFRA-03's "worker connects to Redis broker" criterion is
verifiable: `celery -A app.workers.celery_app call app.workers.tasks.ping`.
"""

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(bind=True, max_retries=3)
def ping(self) -> str:
    """Broker connectivity check — returns 'pong'."""
    logger.info("celery.ping")
    return "pong"
