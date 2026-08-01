"""Credential-to-tenant resolvers so RLS can be enforced against the app role

Migration 0006 forced row-level security, which is only meaningful once the
application stops connecting as a superuser. The blocker is that four lookups
must run *before* any tenant is known, because their whole job is to work out
which tenant a credential belongs to:

    login             users.email_hash          -> tenant
    login             users.username_hash       -> tenant
    QR scan / review  branches.qr_code_token    -> tenant
    token refresh     sessions.refresh_token_hash -> tenant

Under RLS those return zero rows, so login and QR scanning fail silently. A
fifth case, resolving a user from an access token, needs nothing here: the
local JWT carries a `tenant_id` claim we signed ourselves, so the caller binds
tenant context from the claim and the subsequent row read confirms the pairing
(a tampered claim simply finds no user and 401s).

## Why a separate role owns these

SECURITY DEFINER runs a function as its owner. With FORCE ROW LEVEL SECURITY
the table owner is itself subject to the policies, so a function owned by the
table owner would be filtered by the very policy it exists to step around.
The owner therefore has to be a role holding BYPASSRLS.

`quickbite_bootstrap` is NOLOGIN: it cannot open a connection, and exists only
to own these four functions. That keeps the BYPASSRLS blast radius to four
STABLE functions that each accept one high-entropy credential and return one
uuid — no rows, no PII, no way to enumerate a tenant's data. Compare that with
granting BYPASSRLS to the application role itself, which would silently
disable every isolation policy in the database.

`SET search_path` is pinned on every function. Without it, a caller could
create a `users` table in a schema earlier in their search_path and have the
definer function read that instead — the standard SECURITY DEFINER hijack.

These functions do reveal whether a given hash exists, but that is not new:
the login and OTP endpoints already own enumeration behaviour at the HTTP
layer (generic 401s, `status: new_user` for unknown phones), and a caller who
can invoke these already holds the credential being checked.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-30
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOOTSTRAP_ROLE = "quickbite_bootstrap"
APP_ROLE = "quickbite_app"

# (function name, argument name, argument type, source table, lookup column)
RESOLVERS: tuple[tuple[str, str, str, str, str], ...] = (
    ("tenant_for_user_email_hash", "p_email_hash", "text", "restaurant.users", "email_hash"),
    (
        "tenant_for_user_username_hash",
        "p_username_hash",
        "text",
        "restaurant.users",
        "username_hash",
    ),
    (
        "tenant_for_branch_qr_token",
        "p_qr_token",
        "text",
        "restaurant.branches",
        "qr_code_token",
    ),
    (
        "tenant_for_session_token_hash",
        "p_token_hash",
        "text",
        "restaurant.sessions",
        "refresh_token_hash",
    ),
)


def upgrade() -> None:
    # NOLOGIN + BYPASSRLS. Created here rather than in create_app_role.py so the
    # functions below always have an owner to be assigned to, regardless of
    # whether the operator ran that script first.
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{BOOTSTRAP_ROLE}') THEN "
        f"CREATE ROLE {BOOTSTRAP_ROLE} NOLOGIN BYPASSRLS; "
        f"ELSE ALTER ROLE {BOOTSTRAP_ROLE} NOLOGIN BYPASSRLS; "
        f"END IF; END $$;"
    )

    # ALTER FUNCTION ... OWNER TO requires *membership* in the target role, not
    # merely the CREATEROLE privilege that made it. On a self-hosted superuser
    # this never surfaces; on Supabase the migration dies with
    # `must be able to SET ROLE "quickbite_bootstrap"` at the first ALTER OWNER.
    # Granting membership to the migrating role is what makes the ownership
    # transfer legal, and it is safe: the role is NOLOGIN, so membership confers
    # no way to authenticate as it.
    op.execute(f"GRANT {BOOTSTRAP_ROLE} TO CURRENT_USER")

    op.execute("CREATE SCHEMA IF NOT EXISTS bootstrap")
    # CREATE as well as USAGE: Postgres requires a function's owner to hold
    # CREATE on the containing schema, so without it the ALTER ... OWNER TO
    # below fails with `permission denied for schema bootstrap`.
    op.execute(f"GRANT USAGE, CREATE ON SCHEMA bootstrap TO {BOOTSTRAP_ROLE}")

    # BYPASSRLS exempts the role from row *security*, not from ordinary GRANTs,
    # so the definer still needs read access to the tables it resolves against.
    # Granted per column rather than per table: the role can read exactly the
    # lookup key and the tenant_id it returns, and nothing else. A plain
    # `GRANT SELECT ON restaurant.users` would give a BYPASSRLS role sight of
    # every password hash and encrypted email in the system.
    op.execute(f"GRANT USAGE ON SCHEMA restaurant TO {BOOTSTRAP_ROLE}")
    op.execute(
        f"GRANT SELECT (tenant_id, email_hash, username_hash) "
        f"ON restaurant.users TO {BOOTSTRAP_ROLE}"
    )
    op.execute(
        f"GRANT SELECT (tenant_id, qr_code_token) ON restaurant.branches TO {BOOTSTRAP_ROLE}"
    )
    op.execute(
        f"GRANT SELECT (tenant_id, refresh_token_hash) "
        f"ON restaurant.sessions TO {BOOTSTRAP_ROLE}"
    )

    for name, arg, arg_type, table, column in RESOLVERS:
        op.execute(
            f"CREATE OR REPLACE FUNCTION bootstrap.{name}({arg} {arg_type}) "  # nosec B608
            f"RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER "
            # Pinned search_path: without it a caller could create their own
            # `users` table in an earlier schema and have the definer function
            # read that instead — the classic SECURITY DEFINER hijack.
            f"SET search_path = pg_catalog, restaurant "
            f"AS $func$ SELECT tenant_id FROM {table} WHERE {column} = {arg} LIMIT 1 $func$;"
        )
        op.execute(f"ALTER FUNCTION bootstrap.{name}({arg_type}) OWNER TO {BOOTSTRAP_ROLE}")
        # PUBLIC gets EXECUTE on new functions by default; revoke before
        # granting so only the application role can call them.
        op.execute(f"REVOKE ALL ON FUNCTION bootstrap.{name}({arg_type}) FROM PUBLIC")

    # Granted only if the app role exists — a fresh clone may run migrations
    # before create_app_role.py, and a missing role must not fail the migration.
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"GRANT USAGE ON SCHEMA bootstrap TO {APP_ROLE}; "
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA bootstrap TO {APP_ROLE}; "
        f"END IF; END $$;"
    )


def downgrade() -> None:
    for name, _arg, arg_type, _table, _column in RESOLVERS:
        op.execute(f"DROP FUNCTION IF EXISTS bootstrap.{name}({arg_type})")
    op.execute("DROP SCHEMA IF EXISTS bootstrap CASCADE")

    # DROP ROLE refuses while anything still depends on the role, and the
    # column-level grants above count as dependencies — dropping the functions
    # is not enough. DROP OWNED BY clears both owned objects and granted
    # privileges in this database, which is the only thing that makes the
    # downgrade re-runnable.
    op.execute(
        f"DO $$ BEGIN "  # nosec B608 — identifiers are module constants, never caller input
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{BOOTSTRAP_ROLE}') THEN "
        f"DROP OWNED BY {BOOTSTRAP_ROLE}; "
        f"END IF; END $$;"
    )
    op.execute(f"DROP ROLE IF EXISTS {BOOTSTRAP_ROLE}")
