"""Unit tests for REVIEW-03 — inbound GMB review sync.

`gmb_service` (the HTTP client) and `rls.tenant_context` are always mocked
here — this module's job is dedupe/cursor/orchestration logic, not the GMB
wire format (covered in test_gmb_oauth.py) or RLS binding (TENANT-02).
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models.reputation import GMBProfile
from app.services import review_sync_service
from app.services.gmb_service import GMBNotConnected

TENANT_ID = uuid.uuid4()
BRANCH_ID = uuid.uuid4()


@asynccontextmanager
async def _noop_tenant_context(session, tenant_id):  # noqa: ARG001
    yield


@pytest.fixture(autouse=True)
def no_tenant_context(mocker):
    mocker.patch("app.services.review_sync_service.rls.tenant_context", _noop_tenant_context)


def make_profile(**overrides) -> GMBProfile:
    defaults = {
        "tenant_id": TENANT_ID,
        "branch_id": BRANCH_ID,
        "gmb_account_id": "acct-1",
        "gmb_location_id": "loc-1",
        "encrypted_access_token": "v1:irrelevant",
        "encrypted_refresh_token": "v1:irrelevant",
        "token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "gmb_sync_cursor": None,
        "last_synced_at": None,
        "is_connected": True,
    }
    defaults.update(overrides)
    return GMBProfile(**defaults)


def make_review_raw(review_id: str, *, rating="FIVE", comment="Great food!") -> dict:
    return {
        "reviewId": review_id,
        "starRating": rating,
        "comment": comment,
        "reviewer": {"displayName": "A. Diner"},
        "createTime": "2026-01-01T12:00:00Z",
    }


def scalars_result(values: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock(side_effect=execute_results)
    return session


# --- sync_profile -------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_profile_stores_new_reviews(mocker):
    mocker.patch(
        "app.services.review_sync_service.gmb_service.get_valid_access_token",
        AsyncMock(return_value="access-token"),
    )
    mocker.patch(
        "app.services.review_sync_service.gmb_service.fetch_reviews",
        AsyncMock(return_value={"reviews": [make_review_raw("r1"), make_review_raw("r2")]}),
    )
    profile = make_profile()
    session = make_session([scalars_result([])])  # no reviews known yet

    stored = await review_sync_service.sync_profile(session, profile)

    assert stored == 2
    assert session.add.call_count == 2
    assert profile.gmb_sync_cursor == "r1"  # newest-first, cursor = first seen
    assert profile.last_synced_at is not None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_profile_stops_at_the_stored_cursor(mocker):
    mocker.patch(
        "app.services.review_sync_service.gmb_service.get_valid_access_token",
        AsyncMock(return_value="access-token"),
    )
    fetch = mocker.patch(
        "app.services.review_sync_service.gmb_service.fetch_reviews",
        AsyncMock(return_value={"reviews": [make_review_raw("r3"), make_review_raw("r2")]}),
    )
    profile = make_profile(gmb_sync_cursor="r2")  # r2 was the newest last time
    session = make_session([scalars_result([])])

    stored = await review_sync_service.sync_profile(session, profile)

    assert stored == 1  # only r3 is new
    fetch.assert_awaited_once()  # stops paging once the cursor is reached
    assert profile.gmb_sync_cursor == "r3"


@pytest.mark.asyncio
async def test_sync_profile_dedupes_against_already_stored_reviews(mocker):
    mocker.patch(
        "app.services.review_sync_service.gmb_service.get_valid_access_token",
        AsyncMock(return_value="access-token"),
    )
    mocker.patch(
        "app.services.review_sync_service.gmb_service.fetch_reviews",
        AsyncMock(return_value={"reviews": [make_review_raw("r1"), make_review_raw("already-stored")]}),
    )
    profile = make_profile()
    session = make_session([scalars_result(["already-stored"])])

    stored = await review_sync_service.sync_profile(session, profile)

    assert stored == 1


@pytest.mark.asyncio
async def test_sync_profile_paginates_until_no_next_page_token(mocker):
    mocker.patch(
        "app.services.review_sync_service.gmb_service.get_valid_access_token",
        AsyncMock(return_value="access-token"),
    )
    fetch = mocker.patch(
        "app.services.review_sync_service.gmb_service.fetch_reviews",
        AsyncMock(
            side_effect=[
                {"reviews": [make_review_raw("r2")], "nextPageToken": "page-2"},
                {"reviews": [make_review_raw("r1")]},
            ]
        ),
    )
    profile = make_profile()
    session = make_session([scalars_result([])])

    stored = await review_sync_service.sync_profile(session, profile)

    assert stored == 2
    assert fetch.await_count == 2
    assert fetch.await_args_list[1].args[3] == "page-2"


@pytest.mark.asyncio
async def test_sync_profile_returns_zero_when_not_connected(mocker):
    mocker.patch(
        "app.services.review_sync_service.gmb_service.get_valid_access_token",
        AsyncMock(side_effect=GMBNotConnected),
    )
    profile = make_profile(is_connected=False)
    session = make_session([])

    stored = await review_sync_service.sync_profile(session, profile)

    assert stored == 0
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_sync_profile_star_rating_maps_to_int(mocker):
    mocker.patch(
        "app.services.review_sync_service.gmb_service.get_valid_access_token",
        AsyncMock(return_value="access-token"),
    )
    mocker.patch(
        "app.services.review_sync_service.gmb_service.fetch_reviews",
        AsyncMock(return_value={"reviews": [make_review_raw("r1", rating="THREE")]}),
    )
    profile = make_profile()
    session = make_session([scalars_result([])])

    await review_sync_service.sync_profile(session, profile)

    added_review = session.add.call_args.args[0]
    assert added_review.rating == 3
    assert added_review.source == "google"
    assert added_review.external_review_id == "r1"


# --- sync_tenant / sync_all_connected ------------------------------------------


@pytest.mark.asyncio
async def test_sync_tenant_sums_every_connected_profile(mocker):
    profile_a = make_profile(branch_id=uuid.uuid4())
    profile_b = make_profile(branch_id=uuid.uuid4())
    mocker.patch(
        "app.services.review_sync_service.sync_profile", AsyncMock(side_effect=[3, 5])
    )
    session = make_session([scalars_result([profile_a, profile_b])])

    total = await review_sync_service.sync_tenant(session, TENANT_ID)

    assert total == 8


@pytest.mark.asyncio
async def test_sync_all_connected_walks_every_tenant(mocker):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    mocker.patch(
        "app.services.review_sync_service.sync_tenant", AsyncMock(side_effect=[2, 0])
    )
    tenants_result = scalars_result([tenant_a, tenant_b])
    session = make_session([tenants_result])

    total = await review_sync_service.sync_all_connected(session)

    assert total == 2
