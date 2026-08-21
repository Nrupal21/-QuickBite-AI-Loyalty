"""Unit tests for branch_service.list_branches (BRANCH-01).

Powers the Google Profile Link dashboard page: one branch row, optionally
joined with its google_business_profiles row. DB session is mocked per house
convention (see test_dashboard_analytics.py).
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.db.models.branch import Branch
from app.db.models.reputation import GMBProfile
from app.db.models.user import User
from app.services import branch_service

TENANT_ID = uuid.uuid4()


def make_session(all_rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = all_rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


def make_scalar_session(*scalars) -> MagicMock:
    """One `scalar_one_or_none()` result per call, in order — for the
    get-by-id / regenerate flows, which issue a plain scalar SELECT rather
    than the join `make_session` above mocks."""
    results = []
    for value in scalars:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)
    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(side_effect=results)
    return session


def make_branch(name: str, *, is_active: bool = True) -> Branch:
    branch = Branch(
        tenant_id=TENANT_ID,
        name=name,
        address_hash="irrelevant",
        encrypted_address="irrelevant",
        location="POINT(0 0)",
        qr_code_token=uuid.uuid4().hex,
        is_active=is_active,
    )
    branch.id = uuid.uuid4()
    return branch


def make_profile(branch_id: uuid.UUID, *, is_connected: bool = True, synced=None) -> GMBProfile:
    profile = GMBProfile(
        tenant_id=TENANT_ID,
        branch_id=branch_id,
        gmb_account_id="acct",
        gmb_location_id="loc",
        encrypted_access_token="irrelevant",
        encrypted_refresh_token="irrelevant",
        token_expires_at=datetime.now(UTC),
        is_connected=is_connected,
        last_synced_at=synced,
    )
    return profile


@pytest.mark.asyncio
async def test_lists_a_branch_with_no_gmb_profile_as_not_connected():
    branch = make_branch("Bandra West")
    session = make_session([(branch, None)])

    out = await branch_service.list_branches(session)

    assert len(out) == 1
    assert out[0].id == branch.id
    assert out[0].name == "Bandra West"
    assert out[0].gmb_connected is False
    assert out[0].gmb_last_synced_at is None


@pytest.mark.asyncio
async def test_lists_a_connected_branch_with_its_sync_time():
    branch = make_branch("Koregaon Park")
    synced_at = datetime(2026, 8, 20, 9, 30, tzinfo=UTC)
    profile = make_profile(branch.id, is_connected=True, synced=synced_at)
    session = make_session([(branch, profile)])

    out = await branch_service.list_branches(session)

    assert out[0].gmb_connected is True
    assert out[0].gmb_last_synced_at == synced_at


@pytest.mark.asyncio
async def test_a_disconnected_profile_reports_not_connected_even_though_a_row_exists():
    """gmb_service.disconnect() clears the token but the profile row can still
    exist with is_connected=False — that must not read as connected."""
    branch = make_branch("Andheri East")
    profile = make_profile(branch.id, is_connected=False, synced=None)
    session = make_session([(branch, profile)])

    out = await branch_service.list_branches(session)

    assert out[0].gmb_connected is False


@pytest.mark.asyncio
async def test_lists_multiple_branches_in_query_order():
    b1, b2 = make_branch("Bandra West"), make_branch("Koregaon Park")
    session = make_session([(b1, None), (b2, None)])

    out = await branch_service.list_branches(session)

    assert [b.name for b in out] == ["Bandra West", "Koregaon Park"]


@pytest.mark.asyncio
async def test_no_branches_returns_an_empty_list():
    session = make_session([])

    assert await branch_service.list_branches(session) == []


@pytest.mark.asyncio
async def test_list_branches_includes_the_qr_token():
    branch = make_branch("Bandra West")
    session = make_session([(branch, None)])

    out = await branch_service.list_branches(session)

    assert out[0].qr_code_token == branch.qr_code_token


# --- BRANCH-01: QR code image + regeneration ----------------------------


@pytest.mark.asyncio
async def test_get_qr_png_encodes_the_branchs_own_token():
    branch = make_branch("Bandra West")
    session = make_scalar_session(branch)

    png = await branch_service.get_qr_png(session, branch.id)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_get_qr_png_unknown_branch_returns_404():
    session = make_scalar_session(None)

    with pytest.raises(HTTPException) as exc_info:
        await branch_service.get_qr_png(session, uuid.uuid4())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "BRANCH_NOT_FOUND"


@pytest.mark.asyncio
async def test_regenerate_qr_token_changes_the_token_and_audit_logs():
    branch = make_branch("Bandra West")
    old_token = branch.qr_code_token
    session = make_scalar_session(branch, None)  # branch lookup, then GMBProfile lookup
    owner = User(tenant_id=TENANT_ID, role_id=uuid.uuid4())
    owner.id = uuid.uuid4()

    out = await branch_service.regenerate_qr_token(session, branch.id, owner)

    assert out.qr_code_token != old_token
    assert branch.qr_code_token == out.qr_code_token
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_regenerate_qr_token_unknown_branch_returns_404():
    session = make_scalar_session(None)
    owner = User(tenant_id=TENANT_ID, role_id=uuid.uuid4())
    owner.id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        await branch_service.regenerate_qr_token(session, uuid.uuid4(), owner)

    assert exc_info.value.status_code == 404
