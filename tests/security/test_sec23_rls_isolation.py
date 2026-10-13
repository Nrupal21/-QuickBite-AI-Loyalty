"""SEC-23 — unit tests for scripts/verify_rls_isolation.py.

The script itself runs against a live staging database (no staging exists yet),
so these tests pin the logic that decides whether a table PASSES: above all
that an empty table can never be reported as proof of isolation, and that a
superuser/BYPASSRLS connection aborts the run instead of producing a
meaningless green result.
"""

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "verify_rls_isolation", Path(__file__).resolve().parents[2] / "scripts" / "verify_rls_isolation.py"
)
rls = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rls)

TENANT_A, TENANT_B = uuid.uuid4(), uuid.uuid4()


def make_result(**overrides) -> "rls.TableResult":
    defaults = {
        "table": "restaurant.customer_reviews",
        "forced": True,
        "has_tenant_id": True,
        "truth_a": 5,
        "truth_b": 7,
        "a_sees_b": 0,
        "b_sees_a": 0,
        "unbound_rows": 0,
        "a_sees_own": 5,
    }
    defaults.update(overrides)
    return rls.TableResult(**defaults)


def test_isolated_table_with_data_for_both_tenants_passes():
    assert rls.classify(make_result()) == rls.PASS


def test_a_seeing_any_b_row_is_a_leak():
    assert rls.classify(make_result(a_sees_b=1)) == rls.LEAK


def test_b_seeing_any_a_row_is_a_leak():
    assert rls.classify(make_result(b_sees_a=3)) == rls.LEAK


def test_rows_visible_with_no_tenant_bound_is_a_leak():
    """A policy that fails open when app.tenant_id is unset."""
    assert rls.classify(make_result(unbound_rows=2)) == rls.LEAK


def test_empty_table_is_inconclusive_never_pass():
    """'A saw 0 of B's rows' is meaningless if B has no rows there (AC1)."""
    assert rls.classify(make_result(truth_b=0)) == rls.INCONCLUSIVE
    assert rls.classify(make_result(truth_a=0, a_sees_own=0)) == rls.INCONCLUSIVE


def test_without_ground_truth_the_best_result_is_unverified():
    result = make_result(truth_a=None, truth_b=None, a_sees_own=None)
    assert rls.classify(result) == rls.UNVERIFIED


def test_table_without_force_rls_fails_even_when_no_rows_leak():
    assert rls.classify(make_result(forced=False)) == rls.FAIL


def test_policy_that_over_blocks_the_owning_tenant_fails():
    assert rls.classify(make_result(a_sees_own=0)) == rls.FAIL


def test_rls_table_without_tenant_id_column_is_flagged_for_review():
    assert rls.classify(make_result(has_tenant_id=False)) == rls.FAIL


def test_leak_takes_precedence_over_an_empty_table():
    assert rls.classify(make_result(truth_b=0, a_sees_b=1)) == rls.LEAK


def test_exit_code_reflects_the_worst_result():
    ok, empty, leak = make_result(), make_result(truth_b=0), make_result(a_sees_b=1)
    for r in (ok, empty, leak):
        r.verdict = rls.classify(r)

    assert rls.exit_code([ok], [], []) == 0
    assert rls.exit_code([ok, empty], [], []) == 2
    assert rls.exit_code([ok, empty, leak], [], []) == 1


def test_a_tenant_table_without_rls_fails_the_run():
    ok = make_result()
    ok.verdict = rls.classify(ok)

    assert rls.exit_code([ok], ["restaurant.some_new_table"], []) == 1


def test_a_failed_api_check_fails_the_run():
    ok = make_result()
    ok.verdict = rls.classify(ok)

    assert rls.exit_code([ok], [], [("GET /api/v1/reviews", rls.LEAK, "shared=2")]) == 1


def test_report_lists_every_table_and_the_table_count_drift():
    results = [make_result(table="restaurant.users"), make_result(table="payment.invoices", truth_b=0)]
    for r in results:
        r.verdict = rls.classify(r)

    report = rls.render_report("quickbite_app", results, ["restaurant.new_table"], [], TENANT_A, TENANT_B)

    assert "`restaurant.users`" in report and "`payment.invoices`" in report
    assert "**INCONCLUSIVE**" in report
    assert "RLS tables discovered: **2** (ticket text says 15)" in report
    assert "`restaurant.new_table`" in report  # tenant-scoped table with no RLS is called out


@pytest.mark.asyncio
async def test_superuser_app_connection_aborts_the_run(mocker):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"name": "postgres", "rolsuper": True, "rolbypassrls": True})
    conn.close = AsyncMock()
    mocker.patch.object(rls.asyncpg, "connect", AsyncMock(return_value=conn))

    with pytest.raises(SystemExit) as exc:
        await rls.verify_database("postgresql://x", None, TENANT_A, TENANT_B)

    assert "exempt from RLS" in str(exc.value)
    conn.close.assert_awaited()


@pytest.mark.asyncio
async def test_bypassrls_app_connection_aborts_the_run(mocker):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"name": "svc", "rolsuper": False, "rolbypassrls": True})
    conn.close = AsyncMock()
    mocker.patch.object(rls.asyncpg, "connect", AsyncMock(return_value=conn))

    with pytest.raises(SystemExit):
        await rls.verify_database("postgresql://x", None, TENANT_A, TENANT_B)


def test_identifier_quoting_escapes_embedded_quotes():
    assert rls._q('we"ird') == '"we""ird"'
