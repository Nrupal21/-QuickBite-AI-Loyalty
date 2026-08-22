"""Unit tests for identity_link_service — the JIT provisioning / explicit
linking logic that decides which local principal an external token maps to.

DB session is mocked per house convention (see test_rbac.py's make_session).
rls.set_tenant_context/clear_tenant_context are mocked as no-ops so the
execute-result list only needs to account for the service's own queries.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.principal import AuthProvider, SubjectType
from app.db.models.customer import Customer
from app.db.models.identity_link import IdentityLink
from app.db.models.user import User
from app.services import identity_link_service as svc

TENANT_ID = uuid.uuid4()


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    results = []
    for value in execute_results:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)
    session.execute = AsyncMock(side_effect=results)
    return session


def added_instances(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


def make_link(**overrides) -> IdentityLink:
    defaults = {
        "provider": AuthProvider.SUPABASE.value,
        "provider_subject_hash": svc.subject_hash(AuthProvider.SUPABASE, "sub-123"),
        "encrypted_provider_subject": "v1:irrelevant",
        "subject_type": SubjectType.USER.value,
        "local_id": uuid.uuid4(),
        "tenant_id": TENANT_ID,
        "linked_via": svc.LINKED_VIA_EXPLICIT,
        "is_active": True,
    }
    defaults.update(overrides)
    link = IdentityLink(**defaults)
    link.id = uuid.uuid4()
    return link


def make_user(**overrides) -> User:
    defaults = {"tenant_id": TENANT_ID, "role_id": uuid.uuid4(), "is_active": True}
    defaults.update(overrides)
    user = User(**defaults)
    user.id = uuid.uuid4()
    return user


def make_customer(**overrides) -> Customer:
    defaults = {"tenant_id": TENANT_ID, "is_blocked": False}
    defaults.update(overrides)
    customer = Customer(**defaults)
    customer.id = uuid.uuid4()
    return customer


def make_request(tenant_id=TENANT_ID) -> MagicMock:
    request = MagicMock()
    request.state = MagicMock()
    request.state.tenant_id = tenant_id
    return request


@pytest.fixture(autouse=True)
def _mock_rls(mocker):
    mocker.patch("app.services.identity_link_service.rls.set_tenant_context", AsyncMock())
    mocker.patch("app.services.identity_link_service.rls.clear_tenant_context", AsyncMock())


@pytest.fixture(autouse=True)
def _resolved_tenant(mocker):
    """Stub the external-subject->tenant resolver.

    `identity_links` is RLS-protected (migration 0008), so _get_link now
    resolves which tenant owns an external subject before reading the row —
    the same bootstrap pattern as login and QR scanning. The resolver issues
    its own session.execute, so stubbing it keeps make_session's result list
    describing only the ORM queries these tests reason about.
    """
    return mocker.patch(
        "app.services.identity_link_service.bootstrap.tenant_for_identity_link",
        AsyncMock(return_value=TENANT_ID),
    )


# --- subject_hash ---------------------------------------------------------


def test_subject_hash_differs_by_provider_for_the_same_raw_subject():
    """The provider prefix inside the hash is what stops a Firebase subject
    from colliding with a Supabase one that happens to share the same string."""
    supabase_hash = svc.subject_hash(AuthProvider.SUPABASE, "same-subject-value")
    firebase_hash = svc.subject_hash(AuthProvider.FIREBASE, "same-subject-value")
    assert supabase_hash != firebase_hash


def test_subject_hash_is_deterministic():
    assert svc.subject_hash(AuthProvider.SUPABASE, "x") == svc.subject_hash(
        AuthProvider.SUPABASE, "x"
    )


# --- resolve(): existing link ---------------------------------------------


@pytest.mark.asyncio
async def test_resolve_returns_principal_for_active_user_link():
    link = make_link(subject_type=SubjectType.USER.value)
    user = make_user()
    link.local_id = user.id
    session = make_session([link, user, True])

    principal = await svc.resolve(
        make_request(), session, AuthProvider.SUPABASE, "sub-123", {"iat": 1}
    )

    assert principal.subject_type is SubjectType.USER
    assert principal.user is user
    assert principal.tenant_id == TENANT_ID


@pytest.mark.asyncio
async def test_resolve_rejects_suspended_tenant_behind_a_valid_link():
    """Mirrors test_rbac.py's test_suspended_tenant_returns_403_even_with_valid_token
    for the external (Supabase/Firebase) auth path — a suspended tenant's
    already-issued external token must stop working immediately too."""
    link = make_link(subject_type=SubjectType.USER.value)
    user = make_user()
    link.local_id = user.id
    # get_link, select(User), select(Tenant.is_active) — in that order.
    session = make_session([link, user, False])

    with pytest.raises(HTTPException) as exc_info:
        await svc.resolve(
            make_request(), session, AuthProvider.SUPABASE, "sub-123", {"iat": 1}
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "TENANT_SUSPENDED"


@pytest.mark.asyncio
async def test_resolve_rejects_inactive_link():
    link = make_link(is_active=False)
    session = make_session([link])

    with pytest.raises(HTTPException) as exc_info:
        await svc.resolve(make_request(), session, AuthProvider.SUPABASE, "sub-123", {})

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_resolve_rejects_deactivated_user_behind_a_valid_link():
    link = make_link(subject_type=SubjectType.USER.value)
    user = make_user(is_active=False)
    link.local_id = user.id
    session = make_session([link, user])

    with pytest.raises(HTTPException) as exc_info:
        await svc.resolve(make_request(), session, AuthProvider.SUPABASE, "sub-123", {})

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "ACCOUNT_DEACTIVATED"


@pytest.mark.asyncio
async def test_resolve_rejects_blocked_customer_behind_a_valid_link():
    link = make_link(subject_type=SubjectType.CUSTOMER.value)
    customer = make_customer(is_blocked=True)
    link.local_id = customer.id
    session = make_session([link, customer])

    with pytest.raises(HTTPException) as exc_info:
        await svc.resolve(make_request(), session, AuthProvider.SUPABASE, "sub-123", {})

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "CUSTOMER_BLOCKED"


# --- resolve(): no link -----------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_without_link_and_jit_disabled_returns_403(mocker):
    mocker.patch.object(settings, "EXTERNAL_AUTH_JIT_ENABLED", False)
    session = make_session([None])

    with pytest.raises(HTTPException) as exc_info:
        await svc.resolve(make_request(), session, AuthProvider.SUPABASE, "sub-123", {})

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "IDENTITY_LINK_REQUIRED"


@pytest.mark.asyncio
async def test_resolve_without_link_never_provisions_staff(mocker):
    """There is no code path from an unmatched external token to a new
    restaurant.users row, regardless of JIT — only customers can be JIT'd."""
    mocker.patch.object(settings, "EXTERNAL_AUTH_JIT_ENABLED", True)
    session = make_session([None, None])  # get_link, find_customer(phone=None,email=None)

    principal = await svc.resolve(
        make_request(),
        session,
        AuthProvider.FIREBASE,
        "sub-999",
        {"phone_number": "+919876543210", "phone_verified": False},
    )

    assert principal.subject_type is SubjectType.CUSTOMER
    assert not added_instances(session, User)


# --- JIT customer provisioning ---------------------------------------------


@pytest.mark.asyncio
async def test_jit_links_to_existing_customer_found_by_verified_phone(mocker):
    mocker.patch.object(settings, "EXTERNAL_AUTH_JIT_ENABLED", True)
    existing = make_customer()
    session = make_session([None, existing])  # get_link, find_customer(phone hit)

    principal = await svc.resolve(
        make_request(),
        session,
        AuthProvider.FIREBASE,
        "sub-abc",
        {"phone_number": "+919876543210"},
    )

    assert principal.customer is existing
    links = added_instances(session, IdentityLink)
    assert links[0].linked_via == svc.LINKED_VIA_PHONE
    assert links[0].local_id == existing.id
    # No new customer created — must not add a second Customer row on top of
    # the one already found.
    assert not added_instances(session, Customer)


@pytest.mark.asyncio
async def test_jit_never_links_on_unverified_email(mocker):
    """The account-takeover path this whole module exists to close: an
    unverified email must not attach to (and inherit) an existing customer."""
    mocker.patch.object(settings, "EXTERNAL_AUTH_JIT_ENABLED", True)
    # Only one execute: get_link. _find_customer is never called with a real
    # phone/email, since the unverified email is filtered out before reaching it.
    session = make_session([None])

    principal = await svc.resolve(
        make_request(),
        session,
        AuthProvider.FIREBASE,
        "sub-def",
        {"email": "victim@example.com", "email_verified": False},
    )

    new_customer = added_instances(session, Customer)[0]
    assert new_customer.email_hash is None
    assert new_customer.encrypted_email is None
    assert principal.customer is new_customer


@pytest.mark.asyncio
async def test_jit_links_on_verified_email_when_no_phone(mocker):
    mocker.patch.object(settings, "EXTERNAL_AUTH_JIT_ENABLED", True)
    session = make_session([None, None])  # get_link, find_customer(email hit=None -> new)

    await svc.resolve(
        make_request(),
        session,
        AuthProvider.FIREBASE,
        "sub-ghi",
        {"email": "owner@example.com", "email_verified": True},
    )

    new_customer = added_instances(session, Customer)[0]
    assert new_customer.email_hash is not None


@pytest.mark.asyncio
async def test_jit_requires_a_resolvable_tenant(mocker):
    mocker.patch.object(settings, "EXTERNAL_AUTH_JIT_ENABLED", True)
    session = make_session([None])
    request = MagicMock()
    request.state = MagicMock()
    request.state.tenant_id = None  # subdomain middleware never resolved one

    with pytest.raises(HTTPException) as exc_info:
        await svc.resolve(request, session, AuthProvider.FIREBASE, "sub-jkl", {})

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "TENANT_UNRESOLVED"


@pytest.mark.asyncio
async def test_jit_concurrent_insert_recovers_via_existing_link(mocker):
    """Two concurrent first-requests both see no link; the loser must recover
    by re-reading the row the winner just inserted, not by crashing."""
    mocker.patch.object(settings, "EXTERNAL_AUTH_JIT_ENABLED", True)
    winner_link = make_link(subject_type=SubjectType.CUSTOMER.value)
    winner_customer = make_customer()
    winner_link.local_id = winner_customer.id

    session = make_session(
        [
            None,  # get_link (miss)
            None,  # find_customer (miss)
            winner_link,  # re-SELECT after IntegrityError
            winner_customer,  # _principal_from_link's customer SELECT
        ]
    )
    session.commit = AsyncMock(side_effect=IntegrityError("dup", {}, Exception()))

    principal = await svc.resolve(
        make_request(), session, AuthProvider.FIREBASE, "sub-mno", {"phone_number": "+91123"}
    )

    session.rollback.assert_awaited_once()
    assert principal.customer is winner_customer


# --- explicit linking (staff) -----------------------------------------------


@pytest.mark.asyncio
async def test_link_to_user_success():
    user = make_user()
    session = make_session([])

    link = await svc.link_to_user(session, user, AuthProvider.SUPABASE, "sub-pqr")

    assert link.local_id == user.id
    assert link.linked_via == svc.LINKED_VIA_EXPLICIT
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_link_to_user_conflict_returns_409(mocker):
    user = make_user()
    session = make_session([])
    session.commit = AsyncMock(side_effect=IntegrityError("dup", {}, Exception()))

    with pytest.raises(HTTPException) as exc_info:
        await svc.link_to_user(session, user, AuthProvider.SUPABASE, "sub-stu")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "IDENTITY_ALREADY_LINKED"
    session.rollback.assert_awaited_once()


# --- find_linked_customer (customer_oauth_service's lookup, no provisioning) ---


@pytest.mark.asyncio
async def test_find_linked_customer_returns_none_when_no_link():
    session = make_session([None])

    result = await svc.find_linked_customer(session, AuthProvider.FIREBASE, "sub-new")

    assert result is None


@pytest.mark.asyncio
async def test_find_linked_customer_rejects_inactive_link():
    link = make_link(subject_type=SubjectType.CUSTOMER.value, is_active=False)
    session = make_session([link])

    with pytest.raises(HTTPException) as exc_info:
        await svc.find_linked_customer(session, AuthProvider.FIREBASE, "sub-123")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_find_linked_customer_rejects_a_staff_linked_subject():
    """A public customer-sign-in surface must not fall through to treating a
    staff-linked Google account as 'no customer' — that would start a brand
    new, unrelated customer registration for someone who already has staff
    access."""
    link = make_link(subject_type=SubjectType.USER.value)
    session = make_session([link])

    with pytest.raises(HTTPException) as exc_info:
        await svc.find_linked_customer(session, AuthProvider.FIREBASE, "sub-123")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "STAFF_ACCOUNT_LINKED"


@pytest.mark.asyncio
async def test_find_linked_customer_returns_the_customer_for_an_active_link():
    customer = make_customer()
    link = make_link(subject_type=SubjectType.CUSTOMER.value, local_id=customer.id)
    session = make_session([link, customer])

    result = await svc.find_linked_customer(session, AuthProvider.FIREBASE, "sub-123")

    assert result is customer


@pytest.mark.asyncio
async def test_find_linked_customer_rejects_a_blocked_customer():
    customer = make_customer(is_blocked=True)
    link = make_link(subject_type=SubjectType.CUSTOMER.value, local_id=customer.id)
    session = make_session([link, customer])

    with pytest.raises(HTTPException) as exc_info:
        await svc.find_linked_customer(session, AuthProvider.FIREBASE, "sub-123")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "CUSTOMER_BLOCKED"
