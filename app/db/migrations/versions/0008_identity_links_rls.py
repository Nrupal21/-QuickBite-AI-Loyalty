"""Row-level security for restaurant.identity_links

Migration 0005 defined an `_enable_rls()` helper and then never called it, so
both tables it added shipped unprotected. Only one of them is a defect.

**identity_links — a real gap, closed here.** It carries `tenant_id` and maps
an external Supabase/Firebase subject onto a local user or customer. Without a
policy, any session could read every tenant's identity mappings: which external
accounts belong to which restaurant, and the `local_id` of the row each one
authenticates as.

**projection_outbox — deliberately exempt, left alone.** Its drain worker scans
pending rows across every tenant in one query, which RLS's one-tenant-per-session
model cannot express; `app/db/models/outbox.py` documents this. Enabling a
policy there would silently stop the Firestore mirror from draining rather than
fail loudly, which is worse than the exposure it would close. Closing it
properly needs the drain to read through a definer function, and that belongs
with the Celery work, not here.

## Why this needs a resolver, not just a policy

`identity_link_service._get_link()` looks a row up by
`(provider, provider_subject_hash)` with no tenant filter — deliberately, because
resolving *which tenant an external identity belongs to* is the entire job.
That makes it the fifth bootstrap lookup, alongside the four in migration 0007:
enabling the policy without a resolver would turn every Supabase and Firebase
login into a silent "no such identity", exactly the failure mode 0007 exists to
prevent.

So this adds `bootstrap.tenant_for_identity_link()` on the same pattern — owned
by the NOLOGIN BYPASSRLS role, returning one uuid and nothing else, with a
pinned search_path.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-01
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOOTSTRAP_ROLE = "quickbite_bootstrap"
APP_ROLE = "quickbite_app"
RESOLVER = "tenant_for_identity_link"


def upgrade() -> None:
    # Same fail-closed predicate as migration 0006: current_setting(..., true)
    # returns NULL instead of raising when no tenant is bound, and nullif maps
    # the empty string a reset GUC leaves behind onto NULL too. A NULL
    # predicate matches no rows, so an unscoped query returns nothing rather
    # than erroring mid-transaction.
    op.execute("ALTER TABLE restaurant.identity_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE restaurant.identity_links FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_identity_links ON restaurant.identity_links")
    op.execute(
        "CREATE POLICY tenant_isolation_identity_links ON restaurant.identity_links "
        "USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
    )

    # Column-level again: the definer may read the two lookup keys and the
    # tenant_id it returns, and nothing else. encrypted_provider_subject and
    # local_id stay out of reach of a BYPASSRLS role.
    op.execute(
        f"GRANT SELECT (tenant_id, provider, provider_subject_hash, is_active) "
        f"ON restaurant.identity_links TO {BOOTSTRAP_ROLE}"
    )

    op.execute(
        f"CREATE OR REPLACE FUNCTION bootstrap.{RESOLVER}"  # nosec B608
        f"(p_provider text, p_subject_hash text) "
        f"RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER "
        f"SET search_path = pg_catalog, restaurant "
        f"AS $func$ SELECT tenant_id FROM restaurant.identity_links "
        f"WHERE provider = p_provider AND provider_subject_hash = p_subject_hash "
        # An unlinked identity must not resolve: is_active=false is how an
        # unlink is recorded without losing the audit trail.
        f"AND is_active LIMIT 1 $func$;"
    )
    op.execute(
        f"ALTER FUNCTION bootstrap.{RESOLVER}(text, text) OWNER TO {BOOTSTRAP_ROLE}"
    )
    op.execute(f"REVOKE ALL ON FUNCTION bootstrap.{RESOLVER}(text, text) FROM PUBLIC")

    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"GRANT EXECUTE ON FUNCTION bootstrap.{RESOLVER}(text, text) TO {APP_ROLE}; "
        f"END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS bootstrap.{RESOLVER}(text, text)")
    op.execute(
        f"REVOKE ALL (tenant_id, provider, provider_subject_hash, is_active) "
        f"ON restaurant.identity_links FROM {BOOTSTRAP_ROLE}"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation_identity_links ON restaurant.identity_links")
    op.execute("ALTER TABLE restaurant.identity_links NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE restaurant.identity_links DISABLE ROW LEVEL SECURITY")
