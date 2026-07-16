"""Unit tests for AuthService — one per AUTH-01 acceptance criterion.

DB session, Redis, and SendGrid are mocked per AGENTS.md testing rules.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.db.models.audit import AuditLog
from app.db.models.tenant import Tenant
from app.db.models.user import Role, User
from app.schemas.auth import UserRegister
from app.services.auth_service import PENDING_REGISTRATION_TTL_SECONDS, AuthService

STRONG_PASSWORD = "korma-monsoon-49-bicycle"
BASE_URL = "http://test/api/v1/auth/verify-email"


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


def make_request(**overrides) -> UserRegister:
    data = {
        "email": "owner@marcos.in",
        "password": STRONG_PASSWORD,
        "name": "Marco",
        "restaurant_name": "Marcos Pizzeria",
    }
    data.update(overrides)
    return UserRegister(**data)


def added_instances(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


@pytest.mark.asyncio
async def test_register_valid_returns_verification_email_sent(mocker):
    session = make_session([None])  # no existing user with this email_hash
    mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    send_email = mocker.patch(
        "app.services.auth_service.messaging_service.send_verification_email",
        AsyncMock(return_value=True),
    )

    response = await AuthService(session=session).register(make_request(), BASE_URL)

    assert response.status == "verification_email_sent"
    send_email.assert_awaited_once()
    assert send_email.await_args.args[0] == "owner@marcos.in"
    assert BASE_URL + "?token=" in send_email.await_args.args[1]
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_register_stores_pending_registration_not_user_row(mocker):
    """No User/Tenant row exists before verification — login is impossible."""
    session = make_session([None])
    cache_set = mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    mocker.patch(
        "app.services.auth_service.messaging_service.send_verification_email",
        AsyncMock(return_value=True),
    )

    await AuthService(session=session).register(make_request(), BASE_URL)

    assert added_instances(session, User) == []
    assert added_instances(session, Tenant) == []
    key, payload = cache_set.await_args.args
    assert key.startswith("pending_reg:")
    stored = json.loads(payload)
    assert stored["email"] == "owner@marcos.in"
    assert stored["password_hash"].startswith("$2b$")  # bcrypt, never plaintext
    assert STRONG_PASSWORD not in payload
    assert cache_set.await_args.kwargs["ttl"] == PENDING_REGISTRATION_TTL_SECONDS


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(mocker):
    session = make_session([uuid.uuid4()])  # email_hash already present
    mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).register(make_request(), BASE_URL)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


@pytest.mark.asyncio
async def test_register_weak_password_returns_422_with_hint():
    session = make_session([])

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).register(
            make_request(password="password123"), BASE_URL
        )

    assert exc_info.value.status_code == 422
    error = exc_info.value.detail["error"]
    assert error["code"] == "PASSWORD_TOO_WEAK"
    assert error["suggestions"]  # strength improvement hint present
    session.execute.assert_not_awaited()  # rejected before any DB work


@pytest.mark.asyncio
async def test_register_writes_audit_log(mocker):
    session = make_session([None])
    mocker.patch("app.services.auth_service.cache_service.set", AsyncMock())
    mocker.patch(
        "app.services.auth_service.messaging_service.send_verification_email",
        AsyncMock(return_value=True),
    )

    await AuthService(session=session).register(make_request(), BASE_URL)

    audit_rows = added_instances(session, AuditLog)
    assert len(audit_rows) == 1
    assert audit_rows[0].action == "register_requested"
    metadata = audit_rows[0].event_metadata
    assert "owner@marcos.in" not in json.dumps(metadata)  # no PII in audit trail


@pytest.mark.asyncio
async def test_verify_email_creates_tenant_and_owner_in_one_transaction(mocker):
    owner_role = Role(name="OWNER", level=2, permissions={}, mfa_required=True)
    owner_role.id = uuid.uuid4()
    # execute calls: email re-check -> None, owner role, subdomain free -> None
    session = make_session([None, owner_role, None])
    pending = json.dumps(
        {
            "email": "owner@marcos.in",
            "password_hash": "$2b$12$fakehashfakehashfakehash",
            "name": "Marco",
            "restaurant_name": "Marcos Pizzeria",
        }
    )
    mocker.patch("app.services.auth_service.cache_service.get", AsyncMock(return_value=pending))
    cache_delete = mocker.patch(
        "app.services.auth_service.cache_service.delete", AsyncMock()
    )

    response = await AuthService(session=session).verify_email("some-token")

    assert response.status == "verified"
    assert response.subdomain == "marcos-pizzeria"
    tenants = added_instances(session, Tenant)
    users = added_instances(session, User)
    assert len(tenants) == 1 and len(users) == 1
    assert users[0].tenant_id == tenants[0].id
    assert users[0].email_verified is True
    assert users[0].role_id == owner_role.id
    assert users[0].encrypted_email.startswith("v1:")  # PII encrypted at rest
    audit_rows = added_instances(session, AuditLog)
    assert audit_rows[0].action == "email_verified"
    session.commit.assert_awaited_once()  # tenant + user + audit in ONE transaction
    cache_delete.assert_awaited_once_with("pending_reg:some-token")


@pytest.mark.asyncio
async def test_verify_email_invalid_token_returns_400(mocker):
    session = make_session([])
    mocker.patch("app.services.auth_service.cache_service.get", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).verify_email("expired-token")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "VERIFICATION_TOKEN_INVALID"
