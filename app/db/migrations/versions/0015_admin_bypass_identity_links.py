"""Grant quickbite_admin_bypass SELECT on restaurant.identity_links

user_oauth_service (social sign-in/sign-up for restaurant.users, the third
route on the unified auth surface alongside password and OTP) resolves a
verified Firebase subject to a local account with no tenant context bound
yet — the same "who is this, before we know their tenant" problem
`admin_bypass_context` exists for, and the same reasoning
migration 0010 documents for the `users` table.

Migration 0010 granted the bypass role SELECT/UPDATE on `users` and
`sessions` and SELECT on `audit_logs`, but never `identity_links` — nothing
read it through that role until now. `BYPASSRLS` bypasses row-level security
policies, not table-level GRANTs, so the missing grant surfaces as a plain
"permission denied for table identity_links", not an RLS denial.

SELECT only: every INSERT/UPDATE this module makes against `identity_links`
(linking a newly-verified subject, either to an existing account by email or
a freshly created one) runs under the caller's ordinary `quickbite_app` role
outside the bypass block, which already carries the standard app-role grants
migration 0002 established for the whole `restaurant` schema.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-20
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_BYPASS_ROLE = "quickbite_admin_bypass"


def upgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"GRANT SELECT ON restaurant.identity_links TO {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_BYPASS_ROLE}') THEN "
        f"REVOKE SELECT ON restaurant.identity_links FROM {ADMIN_BYPASS_ROLE}; "
        f"END IF; END $$;"
    )
