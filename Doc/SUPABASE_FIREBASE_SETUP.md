# Moving to Supabase Postgres + Firebase/Firestore

Doc 2 already specifies "PostgreSQL 16 via Supabase" as the primary database,
and the Firestore payment mirror is already implemented (`projection_service.py`,
outbox table from migration 0005). This is an activation runbook, not a rewrite:
no schema changes, same SQLAlchemy models, same migrations.

**Architecture is unchanged.** Postgres stays the source of truth. Firestore is
a read mirror fed by a transactional outbox. Supabase and Firebase are also
identity providers, verified in `supabase_auth.py` / `firebase_auth.py`.

> **Read this first.** Supabase does not give you a superuser. Every
> `tenant_isolation_*` policy therefore becomes live the moment you connect,
> which on localhost it never was. Migration 0007 exists for exactly this
> reason — do not skip it.

---

## 1. Create the Supabase project

1. supabase.com → **New project**. Choose a region near your users
   (`ap-south-1` Mumbai for an Indian restaurant product).
2. Save the database password it generates — it is shown once.
3. **Settings → Database → Connection string → URI.** You need *both*:
   - **Direct** (port `5432`) — migrations, and any DDL.
   - **Transaction pooler** (port `6543`) — the running application.

## 2. Enable PostGIS

Geofencing (`LOYALTY-03`) needs it, and your `.env` currently has
`POSTGIS_ENABLED=False`.

**Database → Extensions →** enable `postgis`. Or over SQL:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```

Then set `POSTGIS_ENABLED=true`.

## 3. Run the migrations (direct connection)

```bash
# .env — migrations use the DIRECT port, never the pooler
MIGRATION_DATABASE_URL=postgresql+asyncpg://postgres:<pw>@db.<ref>.supabase.co:5432/postgres

python -m alembic upgrade head
```

This creates the four schemas, all 16 RLS-protected tables, forces row-level
security (0006), and creates the `bootstrap` credential resolvers (0007).

## 4. Create the application role

The app must **not** connect as the project's `postgres` role. On Supabase that
role owns the tables, and migration 0006 forced RLS, which means the owner is
subject to policies — but it may also carry `BYPASSRLS`, which exempts it
unconditionally. Verify rather than assume:

```sql
SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
```

If either is true, the policies are inert for that role. Provision the
unprivileged one:

```bash
QUICKBITE_APP_DB_PASSWORD='<strong password>' python scripts/create_app_role.py
```

That creates `quickbite_app` as `NOSUPERUSER NOBYPASSRLS`, DML-only on the three
tenant schemas and read-only on `static`. It also verifies the attributes and
refuses to finish if either flag is set.

Then point the app at it, through the pooler:

```bash
DATABASE_URL=postgresql+asyncpg://quickbite_app:<pw>@db.<ref>.supabase.co:6543/postgres
DB_USE_TRANSACTION_POOLER=true
```

`DB_USE_TRANSACTION_POOLER` disables asyncpg's prepared-statement cache. Without
it you get `prepared statement "__asyncpg_stmt_x__" does not exist` — and it
appears under load rather than on the first request, so it reads as flaky
infrastructure rather than as a configuration error.

## 5. Verify isolation actually works

Do not take this on trust; it is silent when wrong, because RLS filters rather
than errors.

```sql
-- as quickbite_app, with a random tenant bound
SELECT set_config('app.tenant_id', gen_random_uuid()::text, true);
SELECT count(*) FROM restaurant.users;   -- must be 0
```

Then confirm the bootstrap path still works — login must find a user even
though no tenant is bound yet:

```sql
SELECT bootstrap.tenant_for_user_email_hash('<sha256 of a real email>');
-- must return that user's tenant_id
```

If the first returns rows, the role is exempt from RLS. If the second returns
NULL for a known user, the grants in migration 0007 did not apply.

## 6. Seed reference data

```bash
python scripts/seed_roles.py
python scripts/seed_plans.py
python scripts/seed_email_templates.py
```

Run these as the **owner**: `static.*` is read-only to `quickbite_app` by
design, so a compromised web process cannot grant itself a role or a plan.

---

## 7. Firebase project

1. console.firebase.google.com → **Add project**.
2. **Build → Firestore Database → Create database.** Production mode; the
   server SDK uses a service account and ignores security rules, so lock client
   access down separately if you ever add one.
3. **Project settings → Service accounts → Generate new private key.** This
   downloads a JSON file.
4. Put it in `.env` as a single line:

```bash
FIREBASE_PROJECT_ID=<project-id>
FIREBASE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}
FIRESTORE_DATABASE=(default)
FIRESTORE_PROJECTION_ENABLED=true
```

`config.py` validates this is parseable JSON with the required keys at startup,
so a mangled paste fails fast rather than at the first projection drain.

> The service-account JSON is a **private key with full project access**. It
> belongs in `.env` (gitignored) or a secrets manager — never in
> `.env.example`, and never in a commit.

## 8. Supabase Auth (only if using Supabase as an identity provider)

```bash
SUPABASE_PROJECT_REF=<ref>          # bare ref, not the URL — validated at startup
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_ANON_KEY=<anon key>
SUPABASE_SERVICE_ROLE_KEY=<service role key>   # server only, never ships to a client
```

Verification is JWKS-based (`jwks_cache.py`), so no extra SDK is needed —
`SUPABASE_PROJECT_REF` alone gates whether the verifier is active.

---

## Checklist

- [ ] PostGIS extension enabled, `POSTGIS_ENABLED=true`
- [ ] `alembic upgrade head` run on the **direct** port (through 0007)
- [ ] `create_app_role.py` run; `rolsuper` and `rolbypassrls` both false
- [ ] `DATABASE_URL` = `quickbite_app` on the **pooler** port
- [ ] `MIGRATION_DATABASE_URL` = owner on the **direct** port
- [ ] `DB_USE_TRANSACTION_POOLER=true`
- [ ] Random-tenant probe returns 0 rows
- [ ] `bootstrap.tenant_for_user_email_hash` returns a tenant for a known user
- [ ] Reference data seeded as the owner
- [ ] Firebase service-account JSON present and parseable
- [ ] Login, QR scan, and customer OTP all exercised end to end

## Known gaps

- **Celery workers** share `app/db/base.py`'s engine. Under `asyncio.run()` per
  task, pooled asyncpg connections stay bound to the loop that made them; the
  outbox drain will need `NullPool` per worker. `DB_USE_TRANSACTION_POOLER=true`
  already selects `NullPool`, so pooler deployments are covered incidentally —
  direct-connection workers are not.
- **`customer.customers` and `payment.*` bootstrap paths** were not audited as
  closely as the four in migration 0007; the OTP flow passes `tenant_id`
  explicitly, so it binds context before reading, but exercise it before trusting it.
