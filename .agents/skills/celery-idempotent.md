---
name: celery-idempotent
description: How to write an idempotent Celery task for QuickBite — prevents duplicate WhatsApp/GMB/email sends on retry
tags: [celery, background-tasks, idempotency, reliability]
---

## When to Use This Skill
- Writing any Celery task that calls an external API (Twilio, SendGrid, OpenAI, GMB)
- Implementing LOYALTY-04 (reward dispatch), REVIEW-02 (GMB response posting)
- Writing campaign tasks (NICE-03)
- SEC-15 (Celery idempotency audit)

## The Core Pattern

```python
# app/tasks/loyalty_tasks.py
from celery import shared_task
import structlog
from app.core.cache import cache_service

logger = structlog.get_logger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def dispatch_reward_notification(
    self,
    customer_id: str,          # always strings (JSON-serializable)
    reward_id: str,
    tenant_id: str,
    redemption_code: str,
    restaurant_name: str,
) -> dict:
    """
    Send WhatsApp or SMS reward notification.
    IDEMPOTENT: will not send duplicate messages on Celery retry.
    """
    log = logger.bind(task_id=self.request.id, reward_id=reward_id, customer_id=customer_id)

    # ── 1. Check idempotency key ────────────────────────────────────────
    idempotency_key = f"reward_sent:{tenant_id}:{reward_id}:{customer_id}"
    if cache_service.exists(idempotency_key):
        log.info("celery.task.skipped.already_sent")
        return {"status": "already_sent"}

    try:
        # ── 2. Get customer details ────────────────────────────────────
        # (use synchronous DB access in Celery — separate from async FastAPI session)
        from app.db.sync import get_sync_session
        with get_sync_session() as session:
            customer = session.get(Customer, UUID(customer_id))
            if not customer:
                log.warning("celery.task.customer_not_found")
                return {"status": "customer_not_found"}

        # ── 3. Send notification ───────────────────────────────────────
        if customer.whatsapp_opt_in:
            whatsapp_client.messages.create(
                from_=f"whatsapp:{settings.TWILIO_WHATSAPP_NUMBER}",
                to=f"whatsapp:{decrypt_pii(customer.encrypted_phone)}",
                content_sid=settings.TWILIO_REWARD_TEMPLATE_SID,
                content_variables=json.dumps({
                    "1": restaurant_name,
                    "2": redemption_code,
                })
            )
            log.info("celery.task.whatsapp.sent")
        else:
            # SMS fallback
            twilio_client.messages.create(
                to=decrypt_pii(customer.encrypted_phone),
                from_=settings.TWILIO_PHONE_NUMBER,
                body=f"Your reward at {restaurant_name}! Code: {redemption_code}"
            )
            log.info("celery.task.sms.sent")

        # ── 4. Set idempotency key AFTER successful send ───────────────
        cache_service.set(idempotency_key, "1", ttl=86400 * 7)  # 7-day window
        return {"status": "sent"}

    except Exception as exc:
        log.error("celery.task.failed", error=str(exc))
        # Retry with exponential backoff — idempotency key NOT set, so retry is safe
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))
```

## Rules
- All task arguments: strings or ints — never UUID objects or datetime objects
- Idempotency key set AFTER successful external API call, never before
- `max_retries=3`, `default_retry_delay=60` on all external API tasks
- Log `task_id=self.request.id` in every task for tracing
- NEVER log PII (phone, email) in task logs

## Triggering the Task
```python
# From FastAPI (async context)
dispatch_reward_notification.apply_async(
    args=[str(customer_id), str(reward_id), str(tenant_id), redemption_code, restaurant_name],
    countdown=0
)
```

## Testing Idempotency
```python
def test_dispatch_reward_idempotent(mock_twilio, mock_redis):
    """Trigger twice — only one SMS sent"""
    task_args = [str(uuid4()), str(uuid4()), str(uuid4()), "ABC123", "Marco's Pizza"]
    dispatch_reward_notification(*task_args)
    dispatch_reward_notification(*task_args)  # second call
    mock_twilio.assert_called_once()          # ← proves idempotency
```
