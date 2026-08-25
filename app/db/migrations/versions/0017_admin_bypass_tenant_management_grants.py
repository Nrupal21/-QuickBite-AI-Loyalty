"""Grant quickbite_admin_bypass access to static.subscription_plans and restaurant.branches

ADMIN-01's Tenant Management rebuild enriches GET /admin/tenants with a
Subscription+SubscriptionPlan join and a per-tenant branch count, both run
inside admin_bypass_context alongside the existing restaurant.users /
restaurant.audit_logs aggregates. Confirmed live (not assumed) via a real
GET /admin/tenants request against this migration's pre-state: the join
fails with `asyncpg.exceptions.InsufficientPrivilegeError: permission
denied for schema static` — migration 0010 granted quickbite_admin_bypass
USAGE on the restaurant schema only, and no migration has ever granted it
anything in the static schema. restaurant.branches has the same gap for a
different reason: 0010 granted schema-level USAGE on restaurant already,
but never a table-level GRANT on branches specifically, because no prior
admin action ever read that table.

Same DO-block-guarded pattern as 0016 (grant only if the role exists — a
fresh clone may run migrations before create_app_role.py).

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-25
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_BYPASS_ROLE = "quickbite_admin_bypass"


def upgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"GRANT USAGE ON SCHEMA static TO {ADMIN_BYPASS_ROLE}; "
        f"GRANT SELECT ON static.subscription_plans TO {ADMIN_BYPASS_ROLE}; "
        f"GRANT SELECT ON restaurant.branches TO {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"REVOKE SELECT ON restaurant.branches FROM {ADMIN_BYPASS_ROLE}; "
        f"REVOKE SELECT ON static.subscription_plans FROM {ADMIN_BYPASS_ROLE}; "
        f"REVOKE USAGE ON SCHEMA static FROM {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )
