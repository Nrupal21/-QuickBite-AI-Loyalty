"""Unit tests for identity_service.identify — the unified identify-first login lookup.

DB session is mocked per AGENTS.md testing rules.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.db.models.audit import AuditLog
from app.schemas.auth import IdentifyRequest
from app.services.identity_service import identify

TENANT_ID = uuid.uuid4()


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    results = []
    for value in execute_results:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)
    session.execute = AsyncMock(side_effect=results)
    return session


def added_instances(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


@pytest.fixture(autouse=True)
def _mock_rls(mocker):
    """identify() binds RLS tenant context via a plain session.execute call
    (see app/db/rls.py) before its own SELECTs — mocking it out here keeps
    that call from consuming a slot in the execute_results lists / inflating
    await_count in the tests below, which describe identify()'s *other*
    queries (staff lookup, customer lookup)."""
    mocker.patch("app.services.identity_service.rls.set_tenant_context", AsyncMock())


@pytest.mark.asyncio
async def test_identify_email_matches_staff_takes_precedence():
    staff_user_id = uuid.uuid4()
    session = make_session([staff_user_id])  # only ONE execute — customer lookup skipped

    response = await identify(
        IdentifyRequest(identifier="owner@marcos.in", tenant_id=TENANT_ID), session
    )

    assert response.found is True
    assert response.account_type == "staff"
    assert response.available_methods == ["password"]
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_identify_email_matches_customer_when_no_staff():
    customer_id = uuid.uuid4()
    session = make_session([None, customer_id])  # staff miss, then customer hit

    response = await identify(
        IdentifyRequest(identifier="regular@customer.in", tenant_id=TENANT_ID), session
    )

    assert response.found is True
    assert response.account_type == "customer"
    assert response.available_methods == ["otp"]


@pytest.mark.asyncio
async def test_identify_phone_only_checks_customers():
    customer_id = uuid.uuid4()
    session = make_session([customer_id])  # single lookup — phones never check `users`

    response = await identify(
        IdentifyRequest(identifier="+919876543210", tenant_id=TENANT_ID), session
    )

    assert response.found is True
    assert response.account_type == "customer"
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_identify_username_matches_staff_takes_precedence():
    staff_user_id = uuid.uuid4()
    session = make_session([staff_user_id])  # only ONE execute — customer lookup skipped

    response = await identify(
        IdentifyRequest(identifier="quickbite_owner", tenant_id=TENANT_ID), session
    )

    assert response.found is True
    assert response.account_type == "staff"
    assert response.available_methods == ["password"]
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_identify_username_matches_customer_when_no_staff():
    customer_id = uuid.uuid4()
    session = make_session([None, customer_id])  # staff miss, then customer hit

    response = await identify(
        IdentifyRequest(identifier="loyal_customer_1", tenant_id=TENANT_ID), session
    )

    assert response.found is True
    assert response.account_type == "customer"
    assert response.available_methods == ["otp"]


@pytest.mark.asyncio
async def test_identify_not_found_returns_false():
    session = make_session([None, None])

    response = await identify(
        IdentifyRequest(identifier="nobody@marcos.in", tenant_id=TENANT_ID), session
    )

    assert response.found is False
    assert response.account_type is None
    assert response.available_methods == []


@pytest.mark.asyncio
async def test_identify_invalid_identifier_shape_returns_422():
    session = make_session([])

    with pytest.raises(HTTPException) as exc_info:
        await identify(IdentifyRequest(identifier="not-an-email-or-phone", tenant_id=TENANT_ID), session)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "IDENTIFIER_INVALID"
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_identify_writes_audit_log_without_pii():
    session = make_session([None, None])

    await identify(IdentifyRequest(identifier="nobody@marcos.in", tenant_id=TENANT_ID), session)

    audit_rows = added_instances(session, AuditLog)
    assert len(audit_rows) == 1
    assert audit_rows[0].action == "identify_lookup"
    assert "nobody@marcos.in" not in json.dumps(audit_rows[0].event_metadata)
