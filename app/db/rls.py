"""QuickBite — Row-Level Security tenant context (TENANT-02).

Every tenant-scoped table carries the policy
`tenant_id = current_setting('app.tenant_id')::uuid` (see migration 0002's
`_enable_rls` helper). That GUC has to be set on the connection before any
query runs, or the policy evaluates against an empty setting and the table
returns **zero rows** — RLS filters rather than errors, so a missing tenant
context looks exactly like "there is no data", with no traceback to follow.

Three callers need it, and only the first one existed before:

1. `get_current_user()` / `resolve_principal()` — per authenticated request.
2. Webhook handlers (Razorpay) — authenticated by HMAC signature, not a bearer
   token, so they never pass through the auth dependency.
3. Celery workers (outbox drain, reconciliation) — no request at all.

`set_config(..., true)` is **transaction-local**, which is what makes tenant
context safe on a pooled connection: it cannot outlive the transaction and
leak into the next request that borrows that connection. The cost is that it
is also erased by every `COMMIT` — and every service in this codebase commits.
The `after_begin` listener below re-applies it to each new transaction on the
same session, so a service that commits mid-request does not silently start
reading zero rows afterwards.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

logger = structlog.get_logger(__name__)

# Module-level so the SQL text is identical on every call site — the AUTH-04
# tests assert on `call.args[0]` / `call.args[1]`, so both arguments must stay
# positional and the params dict must stay exactly {"tenant_id": "<uuid>"}.
_SET_TENANT_SQL = text("SELECT set_config('app.tenant_id', :tenant_id, true)")
_CLEAR_TENANT_SQL = text("SELECT set_config('app.tenant_id', '', true)")

# Key under which the active tenant is remembered on `session.info` so the
# after_begin listener can restore it after a commit.
_SESSION_INFO_KEY = "tenant_id"


async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Bind `app.tenant_id` for the current transaction on `session`.

    The value must always come from the database (the subject row, or
    `identity_links.tenant_id`) and never from a JWT claim: Supabase lets the
    end user write `user_metadata` directly, and even admin-only `app_metadata`
    is an external system asserting *our* tenant boundary.
    """
    session.info[_SESSION_INFO_KEY] = str(tenant_id)
    await session.execute(_SET_TENANT_SQL, {"tenant_id": str(tenant_id)})


async def clear_tenant_context(session: AsyncSession) -> None:
    """Drop tenant context. Subsequent queries see zero rows on RLS tables."""
    session.info.pop(_SESSION_INFO_KEY, None)
    await session.execute(_CLEAR_TENANT_SQL)


@asynccontextmanager
async def tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> AsyncIterator[None]:
    """Scoped tenant context for callers with no authenticated principal.

    Used by the Razorpay webhook and the Celery outbox worker. Clearing on exit
    matters more here than on the request path: a worker reuses one session
    across many tenants' outbox rows, so leaving the last tenant bound would
    let the next row's queries run under the wrong tenant.
    """
    await set_tenant_context(session, tenant_id)
    try:
        yield
    finally:
        await clear_tenant_context(session)


@event.listens_for(SyncSession, "after_begin")
def _reapply_tenant_context(session: SyncSession, transaction, connection) -> None:  # noqa: ANN001, ARG001
    """Re-bind `app.tenant_id` whenever a new transaction begins on a session
    that already had one.

    Without this, the first `await session.commit()` in a service silently
    drops tenant context and every later query on that request returns nothing.
    SQLAlchemy fires this for the *sync* Session that AsyncSession proxies, so
    registering it here covers both APIs.
    """
    tenant_id = session.info.get(_SESSION_INFO_KEY)
    if not tenant_id:
        return
    connection.execute(_SET_TENANT_SQL, {"tenant_id": tenant_id})
