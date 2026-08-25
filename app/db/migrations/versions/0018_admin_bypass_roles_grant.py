"""Grant quickbite_admin_bypass SELECT on static.roles

ADMIN-01's final-review fix wave changes `list_tenants`'s staff_count
aggregate (inside `admin_bypass_context`) to join `static.roles` so
SUPER_ADMIN-role accounts (which always carry a `tenant_id` per User's own
docstring) are excluded from a tenant's staff count. Migration 0017 already
granted `quickbite_admin_bypass` USAGE on the `static` schema and SELECT on
`static.subscription_plans` for the same query's existing plan join, but
never granted anything on `static.roles` — without this grant the new join
fails with `asyncpg.exceptions.InsufficientPrivilegeError: permission denied
for table roles` the same way 0016/0017's missing-grant failures did.

Same DO-block-guarded pattern as 0016/0017 (grant only if the role exists —
a fresh clone may run migrations before create_app_role.py).

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-25
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_BYPASS_ROLE = "quickbite_admin_bypass"


def upgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"GRANT SELECT ON static.roles TO {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"REVOKE SELECT ON static.roles FROM {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )
