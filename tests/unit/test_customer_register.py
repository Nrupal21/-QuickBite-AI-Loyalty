"""Unit tests for customer_service.register — one per NEW-OTP-03 acceptance criterion.

DB session and Redis are mocked per AGENTS.md testing rules.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.encryption import sha256_hex
from app.db.models.customer import Customer
from app.schemas.customers import CustomerRegister
from app.services import customer_service

TENANT_ID = uuid.uuid4()
PHONE = "+919876543210"
TOKEN = "reg-token-abc"


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


def pending_payload(identifier: str, identifier_type: str) -> str:
    return json.dumps({"tenant_id": str(TENANT_ID), "identifier": identifier, "identifier_type": identifier_type})


def added_instances(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


@pytest.mark.asyncio
async def test_register_phone_identifier_creates_customer_and_issues_token(mocker):
    session = make_session([None])  # no existing customer with this phone_hash
    mocker.patch(
        "app.services.customer_service.cache_service.get",
        AsyncMock(return_value=pending_payload(PHONE, "phone")),
    )
    cache_delete = mocker.patch("app.services.customer_service.cache_service.delete", AsyncMock())
    mocker.patch("app.services.customer_service.create_customer_token", return_value="customer-jwt-xyz")

    response, token = await customer_service.register(
        CustomerRegister(registration_token=TOKEN, name="Priya", whatsapp_opt_in=True), session
    )

    assert response.status == "registered"
    assert token == "customer-jwt-xyz"
    customers = added_instances(session, Customer)
    assert len(customers) == 1
    assert customers[0].phone_hash == sha256_hex(PHONE)
    assert customers[0].whatsapp_opt_in is True
    assert customers[0].encrypted_name.startswith("v1:")
    cache_delete.assert_awaited_once_with(f"pending_customer_reg:{TOKEN}")


@pytest.mark.asyncio
async def test_register_email_identifier_without_phone_returns_422(mocker):
    session = make_session([])
    mocker.patch(
        "app.services.customer_service.cache_service.get",
        AsyncMock(return_value=pending_payload("customer@example.com", "email")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await customer_service.register(
            CustomerRegister(registration_token=TOKEN, name="Priya"), session
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "PHONE_REQUIRED"
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_email_identifier_with_phone_succeeds(mocker):
    session = make_session([None])
    mocker.patch(
        "app.services.customer_service.cache_service.get",
        AsyncMock(return_value=pending_payload("customer@example.com", "email")),
    )
    mocker.patch("app.services.customer_service.cache_service.delete", AsyncMock())
    mocker.patch("app.services.customer_service.create_customer_token", return_value="customer-jwt-xyz")

    response, _token = await customer_service.register(
        CustomerRegister(registration_token=TOKEN, name="Priya", phone=PHONE), session
    )

    assert response.status == "registered"
    customers = added_instances(session, Customer)
    assert customers[0].email_hash == sha256_hex("customer@example.com")
    assert customers[0].phone_hash == sha256_hex(PHONE)


@pytest.mark.asyncio
async def test_register_with_username_stores_hash_and_encrypted_value(mocker):
    session = make_session([None, None])  # phone free, username free
    mocker.patch(
        "app.services.customer_service.cache_service.get",
        AsyncMock(return_value=pending_payload(PHONE, "phone")),
    )
    mocker.patch("app.services.customer_service.cache_service.delete", AsyncMock())
    mocker.patch("app.services.customer_service.create_customer_token", return_value="customer-jwt-xyz")

    response, _token = await customer_service.register(
        CustomerRegister(registration_token=TOKEN, name="Priya", username="Priya_99"), session
    )

    assert response.status == "registered"
    customers = added_instances(session, Customer)
    assert customers[0].username_hash == sha256_hex("priya_99")  # lowercased
    assert customers[0].encrypted_username.startswith("v1:")


@pytest.mark.asyncio
async def test_register_duplicate_username_returns_409(mocker):
    session = make_session([None, uuid.uuid4()])  # phone free, username already taken
    mocker.patch(
        "app.services.customer_service.cache_service.get",
        AsyncMock(return_value=pending_payload(PHONE, "phone")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await customer_service.register(
            CustomerRegister(registration_token=TOKEN, name="Priya", username="taken"), session
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "USERNAME_ALREADY_TAKEN"


@pytest.mark.asyncio
async def test_register_duplicate_phone_returns_409(mocker):
    session = make_session([uuid.uuid4()])  # phone_hash already registered
    mocker.patch(
        "app.services.customer_service.cache_service.get",
        AsyncMock(return_value=pending_payload(PHONE, "phone")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await customer_service.register(
            CustomerRegister(registration_token=TOKEN, name="Priya"), session
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "CUSTOMER_ALREADY_REGISTERED"


@pytest.mark.asyncio
async def test_register_expired_token_returns_400(mocker):
    session = make_session([])
    mocker.patch("app.services.customer_service.cache_service.get", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        await customer_service.register(
            CustomerRegister(registration_token="expired-token", name="Priya"), session
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "REGISTRATION_TOKEN_INVALID"
