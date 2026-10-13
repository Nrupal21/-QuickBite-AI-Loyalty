"""SEC-23 — cross-tenant RLS verification against a live (staging) database.

READ-ONLY. Issues only SELECTs (and `set_config` for tenant context) inside
read-only transactions; it never inserts, updates, or deletes, so it is safe to
point at staging with real pilot data.

What it checks, mapped to SEC-23's acceptance criteria:

  AC1  Two real tenants with data   -> per-table ground-truth row counts for
                                       each tenant; a table where the *other*
                                       tenant has 0 rows is reported
                                       INCONCLUSIVE, never PASS (an empty table
                                       cannot demonstrate isolation).
  AC2  As Tenant A, SELECT * on     -> for EVERY table with row security
       each RLS table -> 0 B rows      enabled (discovered from pg_class, not a
                                       hardcoded list — the ticket's "15" has
                                       drifted), bind app.tenant_id = A and
                                       count rows belonging to B. Repeated as
                                       B -> A, and with NO tenant bound (must
                                       see zero tenant-owned rows).
  AC3  GET /api/v1/reviews          -> optional (--base-url + QB_TOKEN_A/B):
                                       each token sees only its own reviews.
  AC4  Super Admin sees both        -> optional (QB_ADMIN_TOKEN):
                                       GET /api/v1/admin/tenants lists A and B.
  AC5  Per-table results documented -> markdown report (--output) to paste into
                                       the SEC-24 report.

Two connections are used on purpose:

  * the APP connection (settings.DATABASE_URL, i.e. `quickbite_app`) is the one
    under test. The script REFUSES TO RUN if that role is a superuser or has
    BYPASSRLS — such a role is exempt from row security, so "0 rows seen" would
    prove nothing (see scripts/create_app_role.py).
  * the GROUND-TRUTH connection (--admin-dsn / QB_VERIFY_ADMIN_DSN, a role that
    bypasses RLS, e.g. the Supabase `postgres` role) only counts rows per
    tenant so a PASS can be told apart from "nothing to see". Without it every
    table can at best be PASS-UNVERIFIED.

Usage (credentials via environment, never argv — argv lands in shell history):

    export QB_VERIFY_ADMIN_DSN='postgresql://postgres:...@host:5432/postgres'
    export QB_TOKEN_A=... QB_TOKEN_B=... QB_ADMIN_TOKEN=...      # optional API checks
    python scripts/verify_rls_isolation.py \\
        --tenant-a <uuid> --tenant-b <uuid> \\
        --base-url https://staging.example.com \\
        --output docs/status/sec23_rls_report.md

Exit code: 0 all PASS · 1 any LEAK/FAIL · 2 no leak but INCONCLUSIVE/UNVERIFIED rows remain.
"""

import argparse
import asyncio
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402
import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402

TENANT_SCHEMAS = ("restaurant", "customer", "payment")
EXPECTED_RLS_TABLES_PER_TICKET = 15  # SEC-23 text; the real count is reported alongside it

PASS = "PASS"
LEAK = "LEAK"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
UNVERIFIED = "PASS-UNVERIFIED"

_DISCOVER_RLS_SQL = """
SELECT n.nspname AS schema, c.relname AS name, c.relforcerowsecurity AS forced,
       EXISTS (SELECT 1 FROM pg_attribute a
                WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped) AS has_tenant_id
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p') AND c.relrowsecurity AND n.nspname = ANY($1::text[])
 ORDER BY 1, 2
"""

_UNPROTECTED_TENANT_TABLES_SQL = """
SELECT n.nspname AS schema, c.relname AS name
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
 WHERE c.relkind IN ('r', 'p') AND NOT c.relrowsecurity AND n.nspname = ANY($1::text[])
 ORDER BY 1, 2
"""


@dataclass
class TableResult:
    table: str
    forced: bool
    has_tenant_id: bool
    truth_a: int | None = None
    truth_b: int | None = None
    a_sees_b: int | None = None  # rows owned by B visible while bound to A
    b_sees_a: int | None = None
    unbound_rows: int | None = None  # tenant-owned rows visible with NO tenant bound
    a_sees_own: int | None = None
    verdict: str = ""
    notes: list[str] = field(default_factory=list)


def classify(r: TableResult) -> str:
    """Pure verdict logic — unit-tested in tests/security/test_sec23_rls_isolation.py."""
    if not r.has_tenant_id:
        r.notes.append("RLS enabled but no tenant_id column: policy needs manual review")
        return FAIL
    if (r.a_sees_b or 0) > 0 or (r.b_sees_a or 0) > 0:
        r.notes.append(f"A saw {r.a_sees_b} of B's rows; B saw {r.b_sees_a} of A's rows")
        return LEAK
    if (r.unbound_rows or 0) > 0:
        r.notes.append(f"{r.unbound_rows} tenant-owned rows visible with no tenant bound (fails open)")
        return LEAK
    if not r.forced:
        r.notes.append("FORCE ROW LEVEL SECURITY is off: the table owner bypasses the policy")
        return FAIL
    if r.truth_a is None or r.truth_b is None:
        r.notes.append("no ground-truth connection: cannot tell isolation from an empty table")
        return UNVERIFIED
    if r.truth_a == 0 or r.truth_b == 0:
        empty = [name for name, n in (("A", r.truth_a), ("B", r.truth_b)) if n == 0]
        r.notes.append(f"tenant {'/'.join(empty)} has 0 rows here: seed data, then re-run")
        return INCONCLUSIVE
    if r.a_sees_own is not None and r.a_sees_own != r.truth_a:
        r.notes.append(f"A sees {r.a_sees_own} own rows but {r.truth_a} exist: policy over-blocks")
        return FAIL
    return PASS


def _q(identifier: str) -> str:
    """Quote an identifier that came from pg_catalog (not user input)."""
    return '"' + identifier.replace('"', '""') + '"'


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _bound_counts(conn: asyncpg.Connection, qualified: str, bind: uuid.UUID | None, other: uuid.UUID):
    """Row counts for one table inside a read-only transaction, optionally bound to a tenant."""
    async with conn.transaction(readonly=True):
        if bind is not None:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(bind))
        # identifiers come from pg_catalog via _q(), never from input
        row = await conn.fetchrow(
            f"SELECT count(*) AS total,"  # nosec B608
            f" count(*) FILTER (WHERE tenant_id = $1) AS other,"
            f" count(*) FILTER (WHERE tenant_id IS NOT NULL) AS owned"
            f" FROM {qualified}",
            other,
        )
    return row["total"], row["other"], row["owned"]


async def verify_database(app_dsn: str, admin_dsn: str | None, tenant_a: uuid.UUID, tenant_b: uuid.UUID):
    app = await asyncpg.connect(app_dsn)
    admin = await asyncpg.connect(admin_dsn) if admin_dsn else None
    try:
        role = await app.fetchrow(
            "SELECT current_user AS name, r.rolsuper, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user"
        )
        if role["rolsuper"] or role["rolbypassrls"]:
            raise SystemExit(
                f"ABORT: app connection role '{role['name']}' is superuser/BYPASSRLS - exempt from RLS, so this "
                "test would prove nothing. Point DATABASE_URL at quickbite_app (scripts/create_app_role.py)."
            )

        tables = await app.fetch(_DISCOVER_RLS_SQL, list(TENANT_SCHEMAS))
        unprotected = await app.fetch(_UNPROTECTED_TENANT_TABLES_SQL, list(TENANT_SCHEMAS))

        results: list[TableResult] = []
        for t in tables:
            qualified = f"{_q(t['schema'])}.{_q(t['name'])}"
            res = TableResult(f"{t['schema']}.{t['name']}", t["forced"], t["has_tenant_id"])
            if t["has_tenant_id"]:
                _, res.a_sees_b, _ = await _bound_counts(app, qualified, tenant_a, tenant_b)
                _, res.b_sees_a, _ = await _bound_counts(app, qualified, tenant_b, tenant_a)
                _, _, res.unbound_rows = await _bound_counts(app, qualified, None, tenant_a)
                res.a_sees_own = (await _bound_counts(app, qualified, tenant_a, tenant_a))[1]
                if admin is not None:
                    # identifiers come from pg_catalog via _q(), never from input
                    truth = await admin.fetchrow(
                        f"SELECT count(*) FILTER (WHERE tenant_id = $1) AS a,"  # nosec B608
                        f" count(*) FILTER (WHERE tenant_id = $2) AS b FROM {qualified}",
                        tenant_a,
                        tenant_b,
                    )
                    res.truth_a, res.truth_b = truth["a"], truth["b"]
            res.verdict = classify(res)
            results.append(res)
        return role["name"], results, [f"{u['schema']}.{u['name']}" for u in unprotected]
    finally:
        await app.close()
        if admin is not None:
            await admin.close()


async def verify_api(base_url: str, tenant_a: uuid.UUID, tenant_b: uuid.UUID) -> list[tuple[str, str, str]]:
    """Returns (check, verdict, detail) rows. Tokens come from the environment."""
    token_a, token_b, token_admin = (os.environ.get(k) for k in ("QB_TOKEN_A", "QB_TOKEN_B", "QB_ADMIN_TOKEN"))
    rows: list[tuple[str, str, str]] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:

        async def get(path: str, token: str) -> httpx.Response:
            return await client.get(path, headers={"Authorization": f"Bearer {token}"})

        if token_a and token_b:
            ra, rb = await get("/api/v1/reviews", token_a), await get("/api/v1/reviews", token_b)
            if ra.status_code != 200 or rb.status_code != 200:
                rows.append(("GET /api/v1/reviews", FAIL, f"status A={ra.status_code} B={rb.status_code}"))
            else:
                ids_a, ids_b = {r["id"] for r in ra.json()}, {r["id"] for r in rb.json()}
                overlap = ids_a & ids_b
                verdict = LEAK if overlap else (INCONCLUSIVE if not ids_a or not ids_b else PASS)
                detail = f"A={len(ids_a)} reviews, B={len(ids_b)}, shared={len(overlap)}"
                rows.append(("GET /api/v1/reviews (A vs B)", verdict, detail))
        else:
            rows.append(("GET /api/v1/reviews", INCONCLUSIVE, "QB_TOKEN_A / QB_TOKEN_B not set"))

        if token_admin:
            r = await get("/api/v1/admin/tenants", token_admin)
            if r.status_code != 200:
                rows.append(("GET /api/v1/admin/tenants", FAIL, f"status {r.status_code}"))
            else:
                seen = {str(t.get("tenant_id") or t.get("id")) for t in r.json().get("tenants", [])}
                ok = {str(tenant_a), str(tenant_b)} <= seen
                rows.append(("Super Admin sees both tenants", PASS if ok else FAIL, f"{len(seen)} tenants listed"))
        else:
            rows.append(("Super Admin sees both tenants", INCONCLUSIVE, "QB_ADMIN_TOKEN not set"))
    return rows


def render_report(role, results, unprotected, api_rows, tenant_a, tenant_b) -> str:
    out = [
        "# SEC-23 RLS cross-tenant verification",
        "",
        f"- Run at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Tenant A: `{tenant_a}`  |  Tenant B: `{tenant_b}`",
        f"- App role under test: `{role}` (confirmed not superuser / not BYPASSRLS)",
        f"- RLS tables discovered: **{len(results)}** (ticket text says {EXPECTED_RLS_TABLES_PER_TICKET})",
        "",
        "## Per-table results (AC2, AC5)",
        "",
        "| Table | FORCE RLS | A rows | B rows | A sees B | B sees A | No-tenant rows | Verdict | Notes |",
        "|-------|-----------|--------|--------|----------|----------|----------------|---------|-------|",
    ]
    fmt = lambda v: "n/a" if v is None else str(v)  # noqa: E731
    for r in results:
        out.append(
            f"| `{r.table}` | {'yes' if r.forced else '**NO**'} | {fmt(r.truth_a)} | {fmt(r.truth_b)} | "
            f"{fmt(r.a_sees_b)} | {fmt(r.b_sees_a)} | {fmt(r.unbound_rows)} | **{r.verdict}** | {'; '.join(r.notes)} |"
        )
    out += ["", "## Tenant-scoped tables WITHOUT row security", ""]
    out += [f"- `{t}`" for t in unprotected] or ["- none"]
    out += ["", "## API checks (AC3, AC4)", "", "| Check | Verdict | Detail |", "|-------|---------|--------|"]
    out += [f"| {c} | **{v}** | {d} |" for c, v, d in api_rows] or ["| (skipped: no --base-url) | | |"]
    return "\n".join(out) + "\n"


def exit_code(results, unprotected, api_rows) -> int:
    verdicts = [r.verdict for r in results] + [v for _, v, _ in api_rows]
    if unprotected or any(v in (LEAK, FAIL) for v in verdicts):
        return 1
    return 2 if any(v in (INCONCLUSIVE, UNVERIFIED) for v in verdicts) else 0


async def _main(args: argparse.Namespace) -> int:
    a, b = uuid.UUID(args.tenant_a), uuid.UUID(args.tenant_b)
    if a == b:
        raise SystemExit("tenant-a and tenant-b must differ")
    admin_dsn = os.environ.get("QB_VERIFY_ADMIN_DSN")
    role, results, unprotected = await verify_database(_plain_dsn(settings.DATABASE_URL), admin_dsn, a, b)
    api_rows = await verify_api(args.base_url, a, b) if args.base_url else []
    report = render_report(role, results, unprotected, api_rows, a, b)
    print(report)
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
    return exit_code(results, unprotected, api_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tenant-a", required=True)
    parser.add_argument("--tenant-b", required=True)
    parser.add_argument("--base-url", help="staging API origin, enables the AC3/AC4 API checks")
    parser.add_argument("--output", help="write the markdown report here (for the SEC-24 report)")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
