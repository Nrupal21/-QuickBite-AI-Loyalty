"""Unit tests for app.db.bootstrap — the credential->tenant resolvers.

These pin the contract between Python and migration 0007. The function names
are strings inside SQL, so a rename on either side is invisible to the type
checker and to ruff, and would surface only as a runtime
`function bootstrap.x does not exist` on the login path.

Isolation behaviour itself is verified against a real database (a mocked
session cannot enforce RLS); what is checked here is that each helper calls the
function it claims to, passes the credential as a bound parameter, and treats a
missing row as None rather than raising.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db import bootstrap

TENANT_ID = uuid.uuid4()

# (helper, SQL function it must call)
RESOLVERS = [
    (bootstrap.tenant_for_user_email_hash, "bootstrap.tenant_for_user_email_hash"),
    (bootstrap.tenant_for_user_username_hash, "bootstrap.tenant_for_user_username_hash"),
    (bootstrap.tenant_for_branch_qr_token, "bootstrap.tenant_for_branch_qr_token"),
    (bootstrap.tenant_for_session_token_hash, "bootstrap.tenant_for_session_token_hash"),
]


def make_session(returned: uuid.UUID | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = returned
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.parametrize(("helper", "sql_function"), RESOLVERS)
@pytest.mark.asyncio
async def test_calls_the_matching_sql_function(helper, sql_function):
    """Guards against a rename drifting apart from migration 0007."""
    session = make_session(TENANT_ID)

    assert await helper(session, "credential-value") == TENANT_ID
    assert sql_function in str(session.execute.await_args.args[0])


@pytest.mark.parametrize(("helper", "_sql"), RESOLVERS)
@pytest.mark.asyncio
async def test_credential_is_bound_not_interpolated(helper, _sql):
    """The credential is caller-supplied, so it travels as a parameter. Also
    keeps a hash out of the statement text that ends up in query logs."""
    session = make_session(TENANT_ID)

    await helper(session, "credential-value")

    statement, params = session.execute.await_args.args
    assert params == {"value": "credential-value"}
    assert "credential-value" not in str(statement)


@pytest.mark.parametrize(("helper", "_sql"), RESOLVERS)
@pytest.mark.asyncio
async def test_unknown_credential_returns_none(helper, _sql):
    """No tenant owns it. Callers translate this into their own not-found
    response — a generic 401 for login, an inactive-QR 400 for a scan — so it
    must never raise."""
    assert await helper(make_session(None), "nonexistent") is None


@pytest.mark.asyncio
async def test_each_resolver_targets_a_distinct_function():
    """Four credentials, four functions — a copy-paste that pointed two helpers
    at the same one would silently resolve usernames against emails."""
    called = []
    for helper, _sql in RESOLVERS:
        session = make_session(TENANT_ID)
        await helper(session, "v")
        called.append(str(session.execute.await_args.args[0]))

    assert len(set(called)) == len(RESOLVERS)
