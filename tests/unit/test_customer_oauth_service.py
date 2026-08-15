"""Unit tests for customer_oauth_service.sign_in — the "Sign in with Google /
Apple / Microsoft / GitHub / Twitter" backend, powered by one Firebase ID
token verification regardless of which button the customer clicked.

Firebase verification and the DB session are mocked per house convention
(see test_identity_link_service.py's make_session).
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.principal import AuthProvider
from app.db.models.customer import Customer
from app.schemas.customer_auth import OAuthNewUserResponse, OAuthSignInRequest, OTPVerifiedResponse
from app.services import customer_oauth_service as svc

TENANT_ID = uuid.uuid4()


def make_session() -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    return session


def make_customer(**overrides) -> Customer:
    defaults = {"tenant_id": TENANT_ID, "phone_hash": "hash", "is_blocked": False}
    defaults.update(overrides)
    customer = Customer(**defaults)
    customer.id = uuid.uuid4()
    customer.total_stamps_alltime = 3
    customer.current_reward_count = 0
    return customer


@pytest.mark.asyncio
async def test_sign_in_rejects_a_token_with_no_subject_claim(mocker):
    mocker.patch(
        "app.services.customer_oauth_service.verify_firebase_token",
        AsyncMock(return_value={"email": "no-sub@example.com"}),
    )
    session = make_session()

    with pytest.raises(HTTPException) as exc_info:
        await svc.sign_in(OAuthSignInRequest(id_token="raw-token", tenant_id=TENANT_ID), session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_sign_in_returns_existing_customer_straight_away(mocker):
    customer = make_customer()
    mocker.patch(
        "app.services.customer_oauth_service.verify_firebase_token",
        AsyncMock(return_value={"sub": "firebase-uid-1", "email": "diner@example.com", "email_verified": True}),
    )
    mocker.patch(
        "app.services.customer_oauth_service.identity_link_service.find_linked_customer",
        AsyncMock(return_value=customer),
    )
    mocker.patch(
        "app.services.customer_oauth_service.create_customer_token", return_value="customer-jwt-xyz"
    )
    session = make_session()

    response, token = await svc.sign_in(
        OAuthSignInRequest(id_token="raw-token", tenant_id=TENANT_ID), session
    )

    assert isinstance(response, OTPVerifiedResponse)
    assert response.customer_id == str(customer.id)
    assert token == "customer-jwt-xyz"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sign_in_first_time_subject_returns_registration_token(mocker):
    mocker.patch(
        "app.services.customer_oauth_service.verify_firebase_token",
        AsyncMock(
            return_value={"sub": "firebase-uid-2", "email": "New.Diner@Example.com", "email_verified": True}
        ),
    )
    mocker.patch(
        "app.services.customer_oauth_service.identity_link_service.find_linked_customer",
        AsyncMock(return_value=None),
    )
    cache_set = mocker.patch("app.services.customer_oauth_service.cache_service.set", AsyncMock())
    session = make_session()

    response, token = await svc.sign_in(
        OAuthSignInRequest(id_token="raw-token", tenant_id=TENANT_ID), session
    )

    assert isinstance(response, OAuthNewUserResponse)
    assert response.email == "new.diner@example.com"  # normalised, matches registration's lookup
    assert token is None
    session.commit.assert_not_awaited()

    cache_set.assert_awaited_once()
    cache_key, cache_value = cache_set.call_args.args[0], cache_set.call_args.args[1]
    assert cache_key == f"pending_customer_reg:{response.registration_token}"
    stored = json.loads(cache_value)
    assert stored["tenant_id"] == str(TENANT_ID)
    assert stored["identifier_type"] == "oauth"
    assert stored["oauth_provider"] == AuthProvider.FIREBASE.value
    assert stored["oauth_subject"] == "firebase-uid-2"
    assert stored["verified_email"] == "new.diner@example.com"


@pytest.mark.asyncio
async def test_sign_in_first_time_subject_with_unverified_email_omits_it(mocker):
    mocker.patch(
        "app.services.customer_oauth_service.verify_firebase_token",
        AsyncMock(return_value={"sub": "firebase-uid-3", "email": "unverified@example.com", "email_verified": False}),
    )
    mocker.patch(
        "app.services.customer_oauth_service.identity_link_service.find_linked_customer",
        AsyncMock(return_value=None),
    )
    mocker.patch("app.services.customer_oauth_service.cache_service.set", AsyncMock())
    session = make_session()

    response, _token = await svc.sign_in(
        OAuthSignInRequest(id_token="raw-token", tenant_id=TENANT_ID), session
    )

    assert response.email is None
