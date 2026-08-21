"""users/sessions.tenant_id nullable + widened RLS for standard (tenant-less) users

Registration no longer creates a Tenant up front (see AuthService.verify_email
/ become_restaurant): a newly verified account is a standard user with role
USER and tenant_id NULL, until they explicitly register a restaurant via
POST /auth/register-restaurant. That needs both tables' tenant_id columns to
accept NULL and their RLS policies to admit those rows — the same
`tenant_id IS NULL OR ...` shape migration 0006 already uses for
audit_logs/billing_events/billing_audit_log, applied here to users/sessions.

Relaxing NOT NULL is an instant, no-rewrite ALTER TABLE — no backfill needed,
since every existing row already carries a concrete tenant_id.

Follows 0006's drop-and-recreate idiom for altering a live policy (Postgres
has no single-statement way to change a policy's USING clause). Both tables
keep FORCE ROW LEVEL SECURITY from 0006, untouched here.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = ("users", "sessions")

_NULL_TENANT_POLICY = (
    "tenant_id IS NULL OR "
    "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
)
_LEGACY_POLICY = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table, "tenant_id", existing_type=sa.UUID(), nullable=True, schema="restaurant"
        )
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON restaurant.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON restaurant.{table} "
            f"USING ({_NULL_TENANT_POLICY})"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON restaurant.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON restaurant.{table} "
            f"USING ({_LEGACY_POLICY})"
        )
        op.alter_column(
            table, "tenant_id", existing_type=sa.UUID(), nullable=False, schema="restaurant"
        )
