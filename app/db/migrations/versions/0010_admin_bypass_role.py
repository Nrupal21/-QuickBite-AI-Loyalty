"""Scoped BYPASSRLS role for the Super Admin panel (ADMIN-01)

TENANT-02 names "BYPASSRLS role for Super Admin" as part of its design, but
granting BYPASSRLS to `quickbite_app` itself — the role every request
connects as — would silently disable every isolation policy in the database
for every request, not just the few admin endpoints that legitimately need
to see across tenants. That is the exact mistake migration 0006's docstring
describes the superuser connection making.

Instead this creates `quickbite_admin_bypass`, NOLOGIN and BYPASSRLS, and
grants `quickbite_app` *membership* in it. Membership alone does not confer
BYPASSRLS — that role attribute is checked against the connection's active
identity (`current_user`), not inherited through membership the way table
grants are — so a request stays on `quickbite_app` with RLS fully enforced
until it explicitly runs `SET LOCAL ROLE quickbite_admin_bypass`
(`app/db/rls.py::admin_bypass_context`). `LOCAL` scopes that elevation to the
current transaction: it cannot outlive the request and leak onto the next
one that borrows the same pooled connection, the same safety property
`app.tenant_id` already relies on (see rls.py's module docstring).

Grants are per-table and narrow, mirroring 0007's column-level grants to
`quickbite_bootstrap`: only what ADMIN-01's force-logout and audit-log
endpoints actually touch. `restaurant.tenants` and read-only endpoints that
don't need cross-tenant visibility (GET /admin/tenants) never need this role
at all — `tenants` was never RLS-protected in the first place (0009).

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-06
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_BYPASS_ROLE = "quickbite_admin_bypass"
APP_ROLE = "quickbite_app"


def upgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"CREATE ROLE {ADMIN_BYPASS_ROLE} NOLOGIN BYPASSRLS; "
        f"ELSE ALTER ROLE {ADMIN_BYPASS_ROLE} NOLOGIN BYPASSRLS; "
        f"END IF; END $$;"
    )

    # Granted only if the app role exists — a fresh clone may run migrations
    # before create_app_role.py, and a missing role must not fail the migration.
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"GRANT {ADMIN_BYPASS_ROLE} TO {APP_ROLE}; "
        f"END IF; END $$;"
    )

    op.execute(f"GRANT USAGE ON SCHEMA restaurant TO {ADMIN_BYPASS_ROLE}")

    # Force-logout (POST /admin/users/{id}/force-logout): read the target
    # User row and flip Session.revoked / User.tokens_valid_from — both across
    # a tenant boundary the caller's own JWT does not carry.
    op.execute(f"GRANT SELECT, UPDATE ON restaurant.users TO {ADMIN_BYPASS_ROLE}")
    op.execute(f"GRANT SELECT, UPDATE ON restaurant.sessions TO {ADMIN_BYPASS_ROLE}")

    # Audit log listing (GET /admin/audit-logs): read-only, and never needs to
    # write through this role — every ADMIN-01 action logs its own entry with
    # tenant_id=NULL (a platform-admin event), which the existing
    # tenant_isolation_audit_logs policy already admits without BYPASSRLS.
    op.execute(f"GRANT SELECT ON restaurant.audit_logs TO {ADMIN_BYPASS_ROLE}")


def downgrade() -> None:
    # DROP OWNED BY clears grants without requiring them to be revoked one by
    # one, matching 0007's downgrade for the same reason: table grants count
    # as a dependency and block a plain DROP ROLE.
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"DROP OWNED BY {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )
    op.execute(f"DROP ROLE IF EXISTS {ADMIN_BYPASS_ROLE}")
