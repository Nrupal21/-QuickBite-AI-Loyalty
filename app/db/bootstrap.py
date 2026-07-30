"""Resolve a credential to its tenant before any tenant context exists.

Four lookups in this codebase have to run before RLS can be scoped, because
working out *which* tenant a credential belongs to is precisely their job:
login by email or username, a public QR token, and a refresh token. Under
row-level security an unscoped query on those tables returns zero rows, so
without these the app silently reports "invalid credentials" for a valid login
and "QR no longer active" for a valid code.

Each call runs a SECURITY DEFINER function from migration 0007, owned by a
NOLOGIN BYPASSRLS role, that returns a single `tenant_id` and nothing else.
The pattern at every call site is the same:

    tenant_id = await bootstrap.tenant_for_user_email_hash(session, email_hash)
    if tenant_id is None:
        ...treat as not found...
    await rls.set_tenant_context(session, tenant_id)
    ...normal ORM query, now scoped and RLS-visible...

The row itself is always read through the ordinary RLS path afterwards, so
these functions widen the trusted surface by exactly one uuid per credential —
never a row, never PII.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_EMAIL_SQL = text("SELECT bootstrap.tenant_for_user_email_hash(:value)")
_USERNAME_SQL = text("SELECT bootstrap.tenant_for_user_username_hash(:value)")
_QR_TOKEN_SQL = text("SELECT bootstrap.tenant_for_branch_qr_token(:value)")
_SESSION_SQL = text("SELECT bootstrap.tenant_for_session_token_hash(:value)")


async def _resolve(session: AsyncSession, statement, value: str) -> uuid.UUID | None:
    result = await session.execute(statement, {"value": value})
    return result.scalar_one_or_none()


async def tenant_for_user_email_hash(session: AsyncSession, email_hash: str) -> uuid.UUID | None:
    """Tenant owning the user with this email hash, or None."""
    return await _resolve(session, _EMAIL_SQL, email_hash)


async def tenant_for_user_username_hash(
    session: AsyncSession, username_hash: str
) -> uuid.UUID | None:
    """Tenant owning the user with this username hash, or None."""
    return await _resolve(session, _USERNAME_SQL, username_hash)


async def tenant_for_branch_qr_token(session: AsyncSession, qr_token: str) -> uuid.UUID | None:
    """Tenant owning the branch behind this QR token, or None.

    Serves the two unauthenticated endpoints — loyalty scan and review
    composer — where the QR token is the only credential the caller has.
    """
    return await _resolve(session, _QR_TOKEN_SQL, qr_token)


async def tenant_for_session_token_hash(
    session: AsyncSession, token_hash: str
) -> uuid.UUID | None:
    """Tenant owning the session with this refresh-token hash, or None.

    A refresh token is opaque and carries no claims, so unlike an access token
    there is nothing to read a tenant from without touching the table.
    """
    return await _resolve(session, _SESSION_SQL, token_hash)
