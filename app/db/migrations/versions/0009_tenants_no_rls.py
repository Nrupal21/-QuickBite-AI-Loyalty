"""Ensure restaurant.tenants carries no row-level security policy

Migration 0006 deliberately excluded `restaurant.tenants` from `RLS_TABLES`,
with an explicit rationale in its docstring: tenants is the tenant registry
itself — the row that *defines* a tenant boundary cannot be filtered by that
same boundary without making registration unresolvable (there is no
`tenant_id` column to scope by; the table's own `id` *is* the tenant).

No migration in this history has ever run `ENABLE ROW LEVEL SECURITY` on this
table. Despite that, the live database has RLS active on it with zero
policies attached — almost certainly Supabase's dashboard "Enable RLS"
default being applied to every `public`-adjacent table at some point outside
this migration history, not anything this codebase asked for. Under
`FORCE`-independent Postgres semantics, RLS-enabled-with-no-policy denies
every statement from a non-owner role by default, so `quickbite_app` — never
the table owner — cannot INSERT a new tenant at all. That silently breaks
`AuthService.verify_email()`, the one and only place a `restaurant.tenants`
row is ever created (AUTH-01 registration).

`DISABLE ROW LEVEL SECURITY` is idempotent (a no-op if it was already off)
and restores exactly the state every prior migration already assumed this
table was in.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-06
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE restaurant.tenants DISABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Deliberately a no-op: re-enabling RLS with no policy here would just
    # reintroduce the bug this migration exists to close (registration is the
    # only writer of this table, via the RLS-restricted quickbite_app role).
    pass
