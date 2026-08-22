"""Grant quickbite_admin_bypass access to payment.subscriptions

ADMIN-01's Super Admin panel needs two new capabilities that read/write
payment.subscriptions cross-tenant: overriding a tenant's plan/status
(admin_service.override_subscription) and counting past-due subscriptions
for the health-metrics endpoint (admin_service.get_health_metrics).

Migration 0010 granted quickbite_admin_bypass USAGE on the restaurant
schema only. BYPASSRLS bypasses row-level security policies, not
table-level GRANTs (migration 0007's own docstring makes this point) —
the payment schema was granted only to app_payment_rw (migration 0002),
so quickbite_admin_bypass currently gets "permission denied for schema
payment" the moment it touches payment.subscriptions, RLS bypass or not.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-22
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_BYPASS_ROLE = "quickbite_admin_bypass"


def upgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"GRANT USAGE ON SCHEMA payment TO {ADMIN_BYPASS_ROLE}; "
        f"GRANT SELECT, UPDATE ON payment.subscriptions TO {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"REVOKE SELECT, UPDATE ON payment.subscriptions FROM {ADMIN_BYPASS_ROLE}; "
        f"REVOKE USAGE ON SCHEMA payment FROM {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )
