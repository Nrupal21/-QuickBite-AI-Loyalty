"""Provision the non-superuser role the application must connect as.

Row-level security has two exemptions, and this script closes the one a
migration cannot:

- **Table owner** — exempt unless the table is FORCEd. Migration 0006 does that.
- **Superuser / BYPASSRLS** — exempt *unconditionally*, forced or not.

So `DATABASE_URL=postgresql+asyncpg://postgres:...` defeats every tenant
isolation policy in the database no matter what the migrations say. Postgres
does not warn about this; RLS filters rather than errors, so an exempt
connection looks exactly like a correctly-scoped one that happens to match
every row.

This creates `quickbite_app` as NOSUPERUSER NOBYPASSRLS with DML-only rights,
and grants nothing on `static.*` beyond SELECT — seeding reference data is an
operator job, not something a compromised web process should be able to do.

Run as a superuser (the role it creates is the one the app then uses):

    QUICKBITE_APP_DB_PASSWORD='...' python scripts/create_app_role.py

Then point DATABASE_URL at the new role and re-run the migrations as the
*owner*, not as quickbite_app — it deliberately cannot run DDL.

Idempotent: safe to re-run. An existing role has its password and attributes
reset, never dropped, since dropping would cascade to owned objects.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

from app.core.config import settings  # noqa: E402

APP_ROLE = "quickbite_app"
TENANT_SCHEMAS = ("restaurant", "customer", "payment")
PASSWORD_ENV_VAR = "QUICKBITE_APP_DB_PASSWORD"


def _admin_dsn() -> str:
    """asyncpg speaks plain libpq URLs, not SQLAlchemy's +asyncpg dialect form."""
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def provision(password: str) -> None:
    connection = await asyncpg.connect(_admin_dsn())
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname = $1", APP_ROLE
        )
        # Password is passed as a quoted literal rather than a bind parameter:
        # CREATE/ALTER ROLE is utility DDL and does not accept parameters.
        # quote_literal_cstr equivalent — asyncpg has no DDL escaping helper, so
        # the password is escaped by doubling single quotes.
        escaped = password.replace("'", "''")
        if exists:
            await connection.execute(
                f"ALTER ROLE {APP_ROLE} WITH LOGIN NOSUPERUSER NOBYPASSRLS "
                f"NOCREATEDB NOCREATEROLE PASSWORD '{escaped}'"  # noqa: S608
            )
            print(f"role {APP_ROLE} already exists — attributes and password reset")
        else:
            await connection.execute(
                f"CREATE ROLE {APP_ROLE} WITH LOGIN NOSUPERUSER NOBYPASSRLS "
                f"NOCREATEDB NOCREATEROLE PASSWORD '{escaped}'"  # noqa: S608
            )
            print(f"created role {APP_ROLE}")

        database = await connection.fetchval("SELECT current_database()")
        await connection.execute(f'GRANT CONNECT ON DATABASE "{database}" TO {APP_ROLE}')

        for schema in (*TENANT_SCHEMAS, "static"):
            await connection.execute(f"GRANT USAGE ON SCHEMA {schema} TO {APP_ROLE}")

        for schema in TENANT_SCHEMAS:
            # DML only. No TRUNCATE (it bypasses RLS entirely) and no DDL, so a
            # compromised app process cannot disable the policies protecting it.
            await connection.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} "
                f"TO {APP_ROLE}"
            )
            await connection.execute(
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {APP_ROLE}"
            )
            # Applies to tables a future migration creates, so a new tenant table
            # is not silently unreachable until someone remembers to re-run this.
            await connection.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
            )
            print(f"granted DML on {schema}.*")

        # Reference data is read-only to the app: roles, plans, and feature
        # flags are operator-managed, and write access here would let a
        # compromised process grant itself a plan or a role level.
        await connection.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA static TO {APP_ROLE}")
        await connection.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA static GRANT SELECT ON TABLES TO {APP_ROLE}"
        )
        print("granted SELECT on static.*")

        is_super = await connection.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = $1", APP_ROLE
        )
        if is_super:
            msg = f"{APP_ROLE} still has SUPERUSER or BYPASSRLS — RLS would not apply"
            raise RuntimeError(msg)

        print(f"\nverified: {APP_ROLE} is NOSUPERUSER and NOBYPASSRLS")
        print("Now set, in .env:")
        print(
            f"  DATABASE_URL=postgresql+asyncpg://{APP_ROLE}:<password>@localhost:5432/{database}"
        )
        print("Keep the owner/superuser DSN for running migrations only.")
    finally:
        await connection.close()


if __name__ == "__main__":
    app_password = os.environ.get(PASSWORD_ENV_VAR)
    if not app_password:
        print(f"error: set {PASSWORD_ENV_VAR} to the password for {APP_ROLE}", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(provision(app_password))
