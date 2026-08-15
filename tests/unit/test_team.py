"""Unit tests for TeamService — invite, accept, list, deactivate (AUTH-04).

This is the flow that makes MANAGER and STAFF accounts exist at all, so the
privilege-escalation guards get the most attention here: an Owner must not be
able to mint a peer, and no one may remove someone at their own rank.

DB session, Redis, and SendGrid are mocked per AGENTS.md testing rules.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.encryption import encrypt_pii, sha256_hex
from app.db.models.audit import AuditLog
from app.db.models.user import Role, Session, User
from app.schemas.team import AcceptInviteRequest, StaffInviteRequest
from app.services.team_service import TeamService

TENANT_ID = uuid.uuid4()
OTHER_TENANT_ID = uuid.uuid4()
OWNER_ROLE_ID = uuid.uuid4()
MANAGER_ROLE_ID = uuid.uuid4()
STAFF_ROLE_ID = uuid.uuid4()
STRONG_PASSWORD = "korma-monsoon-49-bicycle"


def make_result(value):
    """One mocked `session.execute()` return, usable as a scalar, a row list, or
    a scalars() iterable — TeamService reads all three shapes."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.all.return_value = value if isinstance(value, list) else []
    result.scalars.return_value.all.return_value = value if isinstance(value, list) else []
    return result


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(side_effect=[make_result(v) for v in execute_results])
    return session


def make_role(name: str, level: int, role_id: uuid.UUID, mfa_required: bool = True) -> Role:
    role = Role(name=name, level=level, permissions={}, mfa_required=mfa_required)
    role.id = role_id
    return role


OWNER_ROLE = make_role("OWNER", 2, OWNER_ROLE_ID)
MANAGER_ROLE = make_role("MANAGER", 3, MANAGER_ROLE_ID)
STAFF_ROLE = make_role("STAFF", 4, STAFF_ROLE_ID, mfa_required=False)


def make_user(email: str, role_id: uuid.UUID, tenant_id: uuid.UUID = TENANT_ID, **kw) -> User:
    defaults = {
        "tenant_id": tenant_id,
        "role_id": role_id,
        "email_hash": sha256_hex(email),
        "encrypted_email": encrypt_pii(email),
        "hashed_password": "irrelevant",
        "mfa_enabled": False,
        "email_verified": True,
        "is_active": True,
    }
    defaults.update(kw)
    user = User(**defaults)
    user.id = uuid.uuid4()
    return user


def added(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


@pytest.fixture
def mock_invite_email(mocker):
    return mocker.patch(
        "app.services.team_service.messaging_service.send_staff_invite_email",
        AsyncMock(return_value=True),
    )


# --- invite -------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_invites_manager_stores_pending_invite(mocker, mock_invite_email):
    owner = make_user("owner@marcos.in", OWNER_ROLE_ID)
    session = make_session([MANAGER_ROLE, None])  # role lookup, then email-exists check
    cache_set = mocker.patch("app.services.team_service.cache_service.set", AsyncMock())

    response = await TeamService(session=session).invite(
        StaffInviteRequest(email="chef@marcos.in", role="MANAGER"), owner, "http://t/accept"
    )

    assert response.status == "invite_sent"
    assert response.role == "MANAGER"
    key, payload = cache_set.await_args.args
    assert key.startswith("staff_invite:")
    stored = json.loads(payload)
    assert stored["role_id"] == str(MANAGER_ROLE_ID)
    assert stored["invited_by"] == str(owner.id)
    mock_invite_email.assert_awaited_once()
    assert added(session, AuditLog)[-1].action == "staff_invited"


@pytest.mark.asyncio
async def test_invite_binds_tenant_to_the_inviter_not_the_request(mocker, mock_invite_email):
    """An invite must never be able to plant an account in another tenant."""
    owner = make_user("owner@marcos.in", OWNER_ROLE_ID)
    session = make_session([STAFF_ROLE, None])
    cache_set = mocker.patch("app.services.team_service.cache_service.set", AsyncMock())

    await TeamService(session=session).invite(
        StaffInviteRequest(email="waiter@marcos.in", role="STAFF"), owner, "http://t/accept"
    )

    _, payload = cache_set.await_args.args
    assert json.loads(payload)["tenant_id"] == str(TENANT_ID)


@pytest.mark.parametrize("forbidden_role", ["OWNER", "SUPER_ADMIN"])
def test_schema_rejects_non_invitable_roles(forbidden_role):
    """Privilege escalation is blocked at the HTTP boundary, before the service."""
    with pytest.raises(ValidationError):
        StaffInviteRequest(email="someone@marcos.in", role=forbidden_role)


@pytest.mark.asyncio
async def test_service_rejects_non_invitable_role_even_if_schema_bypassed():
    """Defence in depth — the guarantee must not rest on Pydantic alone."""
    owner = make_user("owner@marcos.in", OWNER_ROLE_ID)
    session = make_session([])
    smuggled = StaffInviteRequest.model_construct(email="x@marcos.in", role="SUPER_ADMIN")

    with pytest.raises(HTTPException) as exc_info:
        await TeamService(session=session).invite(smuggled, owner, "http://t/accept")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "ROLE_NOT_INVITABLE"


@pytest.mark.asyncio
async def test_invite_existing_email_returns_409():
    owner = make_user("owner@marcos.in", OWNER_ROLE_ID)
    existing = make_user("chef@marcos.in", STAFF_ROLE_ID)
    session = make_session([MANAGER_ROLE, existing.id])

    with pytest.raises(HTTPException) as exc_info:
        await TeamService(session=session).invite(
            StaffInviteRequest(email="chef@marcos.in", role="MANAGER"), owner, "http://t/accept"
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


# --- accept invite ------------------------------------------------------


def invite_payload(role_id: uuid.UUID, role_name: str, email: str = "chef@marcos.in") -> str:
    return json.dumps(
        {
            "email": email,
            "tenant_id": str(TENANT_ID),
            "role_id": str(role_id),
            "role_name": role_name,
            "invited_by": str(uuid.uuid4()),
        }
    )


@pytest.mark.asyncio
async def test_accept_invite_creates_user_with_the_invited_role(mocker):
    mocker.patch(
        "app.services.team_service.cache_service.get",
        AsyncMock(return_value=invite_payload(MANAGER_ROLE_ID, "MANAGER")),
    )
    mocker.patch("app.services.team_service.cache_service.delete", AsyncMock())
    session = make_session([None, MANAGER_ROLE])  # email-exists, then role lookup

    response = await TeamService(session=session).accept_invite(
        AcceptInviteRequest(token="a" * 32, password=STRONG_PASSWORD, name="Chef Anita")
    )

    assert response.status == "account_created"
    assert response.role == "MANAGER"
    assert response.mfa_required is True
    created = added(session, User)[0]
    assert created.role_id == MANAGER_ROLE_ID
    assert created.tenant_id == TENANT_ID
    # Opening a link sent only to that mailbox proves control of it.
    assert created.email_verified is True
    assert created.is_active is True
    # PII never lands in plaintext.
    assert created.email_hash == sha256_hex("chef@marcos.in")
    assert created.encrypted_email != "chef@marcos.in"


@pytest.mark.asyncio
async def test_accept_invite_hashes_password_with_bcrypt(mocker):
    mocker.patch(
        "app.services.team_service.cache_service.get",
        AsyncMock(return_value=invite_payload(STAFF_ROLE_ID, "STAFF")),
    )
    mocker.patch("app.services.team_service.cache_service.delete", AsyncMock())
    session = make_session([None, STAFF_ROLE])

    await TeamService(session=session).accept_invite(
        AcceptInviteRequest(token="a" * 32, password=STRONG_PASSWORD, name="Waiter Raj")
    )

    created = added(session, User)[0]
    assert created.hashed_password.startswith("$2b$")
    assert STRONG_PASSWORD not in created.hashed_password


@pytest.mark.asyncio
async def test_accept_invite_weak_password_returns_422(mocker):
    """A Manager's weak password is the same foothold as an Owner's."""
    mocker.patch(
        "app.services.team_service.cache_service.get",
        AsyncMock(return_value=invite_payload(MANAGER_ROLE_ID, "MANAGER")),
    )
    session = make_session([])

    with pytest.raises(HTTPException) as exc_info:
        await TeamService(session=session).accept_invite(
            AcceptInviteRequest(token="a" * 32, password="password123", name="Chef")
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "PASSWORD_TOO_WEAK"


@pytest.mark.asyncio
async def test_accept_invite_unknown_token_returns_400(mocker):
    mocker.patch("app.services.team_service.cache_service.get", AsyncMock(return_value=None))
    session = make_session([])

    with pytest.raises(HTTPException) as exc_info:
        await TeamService(session=session).accept_invite(
            AcceptInviteRequest(token="b" * 32, password=STRONG_PASSWORD, name="Nobody")
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "INVITE_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_accept_invite_email_registered_in_the_meantime_returns_409(mocker):
    mocker.patch(
        "app.services.team_service.cache_service.get",
        AsyncMock(return_value=invite_payload(MANAGER_ROLE_ID, "MANAGER")),
    )
    delete_mock = mocker.patch("app.services.team_service.cache_service.delete", AsyncMock())
    session = make_session([uuid.uuid4()])

    with pytest.raises(HTTPException) as exc_info:
        await TeamService(session=session).accept_invite(
            AcceptInviteRequest(token="a" * 32, password=STRONG_PASSWORD, name="Chef")
        )

    assert exc_info.value.status_code == 409
    delete_mock.assert_awaited_once()  # spent token is burned, not left dangling


# --- deactivate ---------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_deactivates_staff_and_revokes_their_sessions():
    owner = make_user("owner@marcos.in", OWNER_ROLE_ID)
    target = make_user("waiter@marcos.in", STAFF_ROLE_ID)
    active_session = Session(
        user_id=target.id,
        tenant_id=TENANT_ID,
        refresh_token_hash="hash",
        ip_address_hash="iphash",
        user_agent="ua",
        expires_at=None,
        revoked=False,
    )
    session = make_session([target, OWNER_ROLE, STAFF_ROLE, [active_session]])

    response = await TeamService(session=session).deactivate(target.id, owner)

    assert response.status == "deactivated"
    assert target.is_active is False
    assert active_session.revoked is True
    assert added(session, AuditLog)[-1].action == "staff_deactivated"


@pytest.mark.asyncio
async def test_owner_cannot_deactivate_another_owner():
    """Strictly-more-senior, not senior-or-equal: no Owner may seize sole control."""
    owner = make_user("owner@marcos.in", OWNER_ROLE_ID)
    co_owner = make_user("coowner@marcos.in", OWNER_ROLE_ID)
    session = make_session([co_owner, OWNER_ROLE, OWNER_ROLE])

    with pytest.raises(HTTPException) as exc_info:
        await TeamService(session=session).deactivate(co_owner.id, owner)

    assert exc_info.value.status_code == 403
    assert co_owner.is_active is True


@pytest.mark.asyncio
async def test_cannot_deactivate_self():
    owner = make_user("owner@marcos.in", OWNER_ROLE_ID)
    session = make_session([])

    with pytest.raises(HTTPException) as exc_info:
        await TeamService(session=session).deactivate(owner.id, owner)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "CANNOT_DEACTIVATE_SELF"


@pytest.mark.asyncio
async def test_cannot_deactivate_a_member_of_another_tenant():
    """Same 404 as a missing id — a distinct error would confirm the id exists."""
    owner = make_user("owner@marcos.in", OWNER_ROLE_ID)
    outsider = make_user("staff@rivals.in", STAFF_ROLE_ID, tenant_id=OTHER_TENANT_ID)
    session = make_session([outsider])

    with pytest.raises(HTTPException) as exc_info:
        await TeamService(session=session).deactivate(outsider.id, owner)

    assert exc_info.value.status_code == 404
    assert outsider.is_active is True


@pytest.mark.asyncio
async def test_deactivate_unknown_user_returns_404():
    owner = make_user("owner@marcos.in", OWNER_ROLE_ID)
    session = make_session([None])

    with pytest.raises(HTTPException) as exc_info:
        await TeamService(session=session).deactivate(uuid.uuid4(), owner)

    assert exc_info.value.status_code == 404


# --- list ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_members_returns_decrypted_emails_and_roles():
    owner = make_user("owner@marcos.in", OWNER_ROLE_ID)
    staff = make_user("waiter@marcos.in", STAFF_ROLE_ID)
    session = make_session([[(owner, OWNER_ROLE), (staff, STAFF_ROLE)]])

    response = await TeamService(session=session).list_members(owner)

    assert [m.role for m in response.members] == ["OWNER", "STAFF"]
    assert [m.email for m in response.members] == ["owner@marcos.in", "waiter@marcos.in"]
    assert all(m.is_active for m in response.members)
