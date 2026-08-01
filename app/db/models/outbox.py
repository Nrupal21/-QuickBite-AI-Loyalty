"""ProjectionOutbox — transactional hand-off from Postgres to Firestore.

Postgres is the payment source of truth; Firestore is a realtime read mirror.
Writing both directly would leave a window where the process dies after the
Postgres commit and before the Firestore call, permanently desynchronising the
mirror with nothing recording the gap.

Instead the row below is inserted *in the same transaction* as the ledger
change, so either both exist or neither does, and a Celery worker drains it
into Firestore with retries. Only one store can be wrong at a time, and it
self-heals on the next drain.

Not RLS-protected, and unlike `identity_links` (which shipped unprotected by
oversight and was fixed in migration 0008) that is deliberate: the drain worker
scans pending rows across every tenant in one query, which RLS's
single-tenant-per-session model cannot express. A policy here would not fail
loudly — it would silently return zero rows and stop the Firestore mirror
draining, which is worse than the exposure it closes.

The exposure is real but bounded: any session holding the app role can read
every tenant's pending payment payloads. Nothing outside billing_service and
the drain worker touches this table, and payloads are already-committed ledger
facts rather than credentials.

TODO(SEC-04): close it by enabling RLS and having the drain read through a
SECURITY DEFINER function owned by `quickbite_bootstrap`, the same pattern the
credential resolvers use in migration 0007. That belongs with the Celery work —
the drain's event-loop and pooling story is unproven, and changing both at once
would make a failure hard to attribute.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# status values
PENDING = "pending"
SENT = "sent"
FAILED = "failed"

# Give up after this many attempts and let reconciliation repair it — an
# infinitely retrying row would hammer Firestore over a permanent error
# (bad credentials, deleted collection) with no one noticing.
MAX_ATTEMPTS = 10


class ProjectionOutbox(Base):
    """One row per Firestore document write that still needs to happen."""

    __tablename__ = "projection_outbox"
    __table_args__ = {"schema": "payment"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    aggregate_type: Mapped[str] = mapped_column(String)  # subscription | invoice | payment
    aggregate_id: Mapped[uuid.UUID] = mapped_column()
    # Monotonic per aggregate. The worker drains with SKIP LOCKED and therefore
    # delivers out of order; the Firestore write drops any version <= the one
    # already stored, which is what stops a stale projection overwriting a
    # fresh one.
    version: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # pre-rendered document body
    status: Mapped[str] = mapped_column(String, default=PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
