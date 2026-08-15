"""Drains payment.projection_outbox into the Firestore payment mirror.

Called by a Celery task on a short interval. Postgres remains the source of
truth throughout — a Firestore write failure here leaves the outbox row
`pending` with an incremented `attempts` and a backed-off `next_attempt_at`;
it never touches the ledger tables billing_service already committed. See
`app/db/models/outbox.py` for why this table carries no RLS policy: draining
requires a cross-tenant scan that a single-tenant RLS session cannot express.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import firestore_client
from app.core.config import settings
from app.db.models.outbox import FAILED, MAX_ATTEMPTS, PENDING, SENT, ProjectionOutbox

logger = structlog.get_logger(__name__)

_BATCH_SIZE = 50
# Exponential-ish backoff: attempt 1 waits 30s, attempt 2 waits 60s, ... A
# permanent error (bad credentials, deleted collection) still gives up at
# MAX_ATTEMPTS rather than retrying forever against something that will never
# succeed.
_BACKOFF_BASE_SECONDS = 30


def _doc_ref(client: Any, row: ProjectionOutbox) -> Any:
    tenant_ref = client.collection("tenants").document(str(row.tenant_id))
    if row.aggregate_type == "subscription":
        # Singleton doc: a tenant has exactly one active subscription, so
        # there is no natural per-aggregate id to key on.
        return tenant_ref.collection("subscription").document("current")
    if row.aggregate_type in ("invoice", "payment"):
        return tenant_ref.collection(f"{row.aggregate_type}s").document(str(row.aggregate_id))
    msg = f"Unknown projection aggregate_type: {row.aggregate_type!r}"
    raise ValueError(msg)


async def _write_with_version_guard(
    client: Any, doc_ref: Any, version: int, payload: dict[str, Any]
) -> bool:
    """Write inside a Firestore transaction that drops a stale version.

    Rows are drained with `SKIP LOCKED`, so delivery order across a single
    aggregate is not guaranteed. Without this guard, a retried older event
    could overwrite a newer one already projected. Ties (`==`) are allowed
    through — a duplicate delivery of the same event is harmless to reapply.
    """
    from google.cloud.firestore_v1.async_transaction import async_transactional  # noqa: PLC0415

    @async_transactional
    async def _txn(transaction: Any, doc_ref: Any, version: int, payload: dict[str, Any]) -> bool:
        snapshot = await doc_ref.get(transaction=transaction)
        existing_version = snapshot.get("version") if snapshot.exists else None
        if existing_version is not None and existing_version > version:
            return False
        transaction.set(
            doc_ref,
            {**payload, "version": version, "updated_at": datetime.now(UTC).isoformat()},
            merge=True,
        )
        return True

    transaction = client.transaction()
    return await _txn(transaction, doc_ref, version, payload)


async def drain_pending(session: AsyncSession, *, batch_size: int = _BATCH_SIZE) -> int:
    """Send up to `batch_size` due outbox rows to Firestore. Returns count sent.

    Safe to run concurrently from multiple workers: rows are claimed with
    `FOR UPDATE SKIP LOCKED`, so two workers never process the same row.
    """
    if not settings.FIRESTORE_PROJECTION_ENABLED:
        return 0

    result = await session.execute(
        select(ProjectionOutbox)
        .where(
            ProjectionOutbox.status == PENDING,
            ProjectionOutbox.next_attempt_at <= datetime.now(UTC),
        )
        .order_by(ProjectionOutbox.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    rows = result.scalars().all()
    if not rows:
        return 0

    client = firestore_client.get_client()
    sent = 0
    for row in rows:
        try:
            written = await _write_with_version_guard(
                client, _doc_ref(client, row), row.version, row.payload
            )
        except Exception as exc:  # noqa: BLE001 - any Firestore failure is a retry candidate
            row.attempts += 1
            row.last_error = str(exc)[:500]
            if row.attempts >= MAX_ATTEMPTS:
                row.status = FAILED
                logger.error(
                    "billing.projection.failed_permanently",
                    outbox_id=str(row.id),
                    tenant_id=str(row.tenant_id),
                    attempts=row.attempts,
                )
            else:
                row.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=_BACKOFF_BASE_SECONDS * row.attempts
                )
                logger.warning(
                    "billing.projection.retry",
                    outbox_id=str(row.id),
                    attempts=row.attempts,
                    error=str(exc),
                )
        else:
            row.status = SENT
            row.sent_at = datetime.now(UTC)
            if not written:
                logger.info(
                    "billing.projection.stale_version_dropped",
                    outbox_id=str(row.id),
                    tenant_id=str(row.tenant_id),
                    version=row.version,
                )
            sent += 1
        await session.commit()
    return sent
