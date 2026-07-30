"""Force row-level security on all 16 tenant-scoped tables

The policies from 0001 and 0002 have never actually filtered anything.
`ENABLE ROW LEVEL SECURITY` does not apply to the table's owner, and every
policy in this database was created without `FORCE`, so any connection owning
the tables sees every tenant's rows regardless of `app.tenant_id`.

Demonstrated before writing this migration: with two tenants inserted and
tenant A bound via `set_config('app.tenant_id', ...)`, a count over
`restaurant.audit_logs` returned **both** rows. SEC-04's core criterion ("As
Tenant A: SELECT * FROM customer_reviews -> 0 Tenant B rows") fails today, and
the failure is silent — RLS filters rather than errors, so an inert policy is
indistinguishable from a correct one until someone counts rows.

FORCE closes the owner gap. It does **not** close the superuser gap: Postgres
exempts superusers and any role with BYPASSRLS from row security
unconditionally, forced or not. The application therefore also has to stop
connecting as a superuser — see scripts/create_app_role.py, which provisions
the NOSUPERUSER/NOBYPASSRLS role the app is meant to use. Both halves are
required; neither alone restores isolation.

Deliberately not touched here: `static.*` (roles, plans, feature flags,
notification templates) is shared reference data with no tenant_id, and
`restaurant.tenants` is the tenant registry itself — the row that *defines* a
tenant cannot be filtered by tenant without making onboarding unresolvable.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-30
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table carrying a tenant_id policy, verified against pg_class.relrowsecurity
# rather than transcribed from the earlier migrations — 0001 and 0002 each
# enabled a different subset, and the union is what matters.
RLS_TABLES: tuple[tuple[str, str], ...] = (
    ("customers", "customer"),
    ("stamp_logs", "customer"),
    ("billing_audit_log", "payment"),
    ("billing_events", "payment"),
    ("invoices", "payment"),
    ("payment_methods", "payment"),
    ("subscriptions", "payment"),
    ("usage_tracking", "payment"),
    ("audit_logs", "restaurant"),
    ("branches", "restaurant"),
    ("customer_reviews", "restaurant"),
    ("google_business_profiles", "restaurant"),
    ("review_responses", "restaurant"),
    ("reward_programs", "restaurant"),
    ("sessions", "restaurant"),
    ("users", "restaurant"),
)


# Tables whose policy also admits platform-level rows with no tenant.
NULL_TENANT_TABLES = frozenset({"audit_logs", "billing_events", "billing_audit_log"})


def _policy_expression(table: str) -> str:
    """Tenant predicate that fails *closed* on unset context instead of erroring.

    The original `current_setting('app.tenant_id')::uuid` raises
    `invalid input syntax for type uuid: ""` the moment a query runs without
    tenant context — it only ever looked fine because the superuser connection
    never reached the policy at all. Two changes fix that:

    - `current_setting(..., true)` (missing_ok) returns NULL rather than
      raising when the GUC was never set in this transaction.
    - `nullif(..., '')` maps the empty string — what a reset GUC leaves behind
      — onto NULL too.

    A NULL predicate matches no rows, so an unscoped query now returns nothing
    instead of a 500. That is the correct default for an isolation control:
    silence is recoverable, an exception mid-transaction is not, and neither
    leaks another tenant's data.
    """
    tenant_match = (
        "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    if table in NULL_TENANT_TABLES:
        return f"tenant_id IS NULL OR {tenant_match}"
    return tenant_match


def upgrade() -> None:
    for table, schema in RLS_TABLES:
        op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {schema}.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {schema}.{table} "
            f"USING ({_policy_expression(table)})"
        )


def downgrade() -> None:
    for table, schema in RLS_TABLES:
        op.execute(f"ALTER TABLE {schema}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {schema}.{table}")
        # Restores 0001/0002's original expression, errors on unset GUC included.
        legacy = "tenant_id = current_setting('app.tenant_id')::uuid"
        if table in NULL_TENANT_TABLES:
            legacy = f"tenant_id IS NULL OR {legacy}"
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {schema}.{table} USING ({legacy})"
        )
