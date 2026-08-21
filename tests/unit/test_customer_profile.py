"""Unit tests for GET /customers/me — "My Rewards" (stamps by location +
review-draft history).

DB session is mocked per AGENTS.md testing rules.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.encryption import encrypt_pii
from app.db.models.customer import Customer
from app.services import customer_service

TENANT_ID = uuid.uuid4()
BANDRA_ID = uuid.uuid4()
ANDHERI_ID = uuid.uuid4()


def make_customer(**overrides) -> Customer:
    defaults = {
        "tenant_id": TENANT_ID,
        "phone_hash": "hash",
        "encrypted_phone": encrypt_pii("+919876543210"),
        "encrypted_name": encrypt_pii("Priya Rao"),
        "encrypted_email": encrypt_pii("priya@example.com"),
        "total_stamps_alltime": 7,
        "current_reward_count": 3,
    }
    defaults.update(overrides)
    customer = Customer(**defaults)
    customer.id = uuid.uuid4()
    customer.created_at = datetime.now(timezone.utc)
    return customer


def make_session(stamp_rows: list, draft_rows: list) -> MagicMock:
    stamps_result = MagicMock()
    stamps_result.all.return_value = stamp_rows
    drafts_result = MagicMock()
    drafts_result.all.return_value = draft_rows

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[stamps_result, drafts_result])
    return session


@pytest.mark.asyncio
async def test_profile_includes_decrypted_identity_and_stamp_totals():
    customer = make_customer()
    session = make_session(stamp_rows=[], draft_rows=[])

    profile = await customer_service.get_profile(session, customer)

    assert profile.customer_id == str(customer.id)
    assert profile.name == "Priya Rao"
    assert profile.email == "priya@example.com"
    assert profile.total_stamps_alltime == 7
    assert profile.current_reward_count == 3


@pytest.mark.asyncio
async def test_stamps_grouped_by_branch_across_locations():
    """"received stamp from different locations" — one row per branch, not
    one row per scan."""
    customer = make_customer()
    now = datetime.now(timezone.utc)
    session = make_session(
        stamp_rows=[
            (BANDRA_ID, "Bandra", encrypt_pii("12 Turner Rd"), 5, now),
            (ANDHERI_ID, "Andheri", encrypt_pii("4 SV Rd"), 2, now),
        ],
        draft_rows=[],
    )

    profile = await customer_service.get_profile(session, customer)

    assert len(profile.stamps_by_location) == 2
    bandra = next(loc for loc in profile.stamps_by_location if loc.branch_name == "Bandra")
    assert bandra.stamp_count == 5
    assert bandra.branch_address == "12 Turner Rd"
    andheri = next(loc for loc in profile.stamps_by_location if loc.branch_name == "Andheri")
    assert andheri.stamp_count == 2


@pytest.mark.asyncio
async def test_no_stamps_yet_returns_empty_list_not_an_error():
    customer = make_customer(total_stamps_alltime=0, current_reward_count=0)
    session = make_session(stamp_rows=[], draft_rows=[])

    profile = await customer_service.get_profile(session, customer)

    assert profile.stamps_by_location == []
    assert profile.recent_review_drafts == []


@pytest.mark.asyncio
async def test_review_drafts_included_with_branch_name_and_excerpt():
    from app.db.models.customer import ReviewDraft

    customer = make_customer()
    draft = ReviewDraft(
        tenant_id=TENANT_ID,
        branch_id=BANDRA_ID,
        customer_id=customer.id,
        rating=5,
        tags=["great biryani", "quick service"],
        draft_excerpt="The butter chicken was rich and properly spiced...",
    )
    draft.id = uuid.uuid4()
    draft.created_at = datetime.now(timezone.utc)
    session = make_session(stamp_rows=[], draft_rows=[(draft, "Bandra")])

    profile = await customer_service.get_profile(session, customer)

    assert len(profile.recent_review_drafts) == 1
    out = profile.recent_review_drafts[0]
    assert out.branch_name == "Bandra"
    assert out.rating == 5
    assert out.tags == ["great biryani", "quick service"]
    assert out.draft_excerpt.startswith("The butter chicken")
