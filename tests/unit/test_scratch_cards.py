"""Unit tests for LoyaltyService's NICE-01 scratch-card methods — one test
per acceptance criterion. Mirrors tests/unit/test_loyalty_scan.py's mocking
style (mocked session, mocked tenant_has_feature) rather than exercising the
full process_scan flow, since these methods have narrow, independently
testable contracts."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.db.models.branch import Branch
from app.db.models.customer import Customer
from app.db.models.loyalty import ScratchCard
from app.db.models.user import User
from app.schemas.loyalty import PrizePoolCreateRequest
from app.services.loyalty_service import LoyaltyService

TENANT_ID = uuid.uuid4()
BRANCH_ID = uuid.uuid4()


def make_branch() -> Branch:
    branch = Branch(
        tenant_id=TENANT_ID,
        name="Test Branch",
        address_hash="a" * 64,
        encrypted_address="encrypted-address-blob",
        location="POINT(77.5946 12.9716)",
        geofence_radius_m=100,
        qr_code_token="valid-token-1234567890",
        is_active=True,
    )
    branch.id = BRANCH_ID
    return branch


def make_customer(**overrides) -> Customer:
    defaults = {
        "tenant_id": TENANT_ID,
        "phone_hash": "phonehash123",
        "total_stamps_alltime": 5,
    }
    defaults.update(overrides)
    customer = Customer(**defaults)
    customer.id = uuid.uuid4()
    return customer


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


def make_scalars_result(labels: list[str]) -> MagicMock:
    scalars = MagicMock()
    scalars.all.return_value = labels
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


# --- _maybe_create_scratch_card --------------------------------------------


@pytest.mark.asyncio
async def test_non_fifth_scan_does_not_create_a_card(mocker):
    branch = make_branch()
    customer = make_customer(total_stamps_alltime=4)
    session = MagicMock()
    service = LoyaltyService(session=session)

    card_id, locked = await service._maybe_create_scratch_card(branch, customer)

    assert card_id is None
    assert locked is False
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_anonymous_scan_never_creates_a_card():
    branch = make_branch()
    session = MagicMock()
    service = LoyaltyService(session=session)

    card_id, locked = await service._maybe_create_scratch_card(branch, None)

    assert card_id is None
    assert locked is False


@pytest.mark.asyncio
async def test_fifth_scan_on_starter_plan_is_locked_not_created(mocker):
    """Acceptance criterion: 'Starter/Free users → 402 upgrade prompt (not
    scratch card screen)' — surfaced here as scratch_card_locked=True, which
    the route layer already threads straight into ScanResponse."""
    branch = make_branch()
    customer = make_customer(total_stamps_alltime=5)
    session = MagicMock()
    mocker.patch(
        "app.services.loyalty_service.tenant_has_feature", AsyncMock(return_value=False)
    )
    service = LoyaltyService(session=session)

    card_id, locked = await service._maybe_create_scratch_card(branch, customer)

    assert card_id is None
    assert locked is True
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_fifth_scan_on_pro_plan_creates_a_card_with_a_drawn_prize(mocker):
    branch = make_branch()
    customer = make_customer(total_stamps_alltime=10)
    session = make_session([])
    session.execute = AsyncMock(return_value=make_scalars_result(["Free Coffee", "10% Off"]))
    mocker.patch("app.services.loyalty_service.tenant_has_feature", AsyncMock(return_value=True))
    # Not mocking secrets.choice: it's the same module object `core.security.
    # generate_redemption_code` also draws from for the 6-char code, so
    # patching it here would corrupt that too. Assert against the real
    # (uniform) draw instead — deterministic enough since the pool has 2
    # entries and this only needs "one of them", not "exactly which one".
    service = LoyaltyService(session=session)

    card_id, locked = await service._maybe_create_scratch_card(branch, customer)

    assert card_id is not None
    assert locked is False
    added = session.add.call_args.args[0]
    assert isinstance(added, ScratchCard)
    assert added.prize_label in ("Free Coffee", "10% Off")
    assert added.customer_id == customer.id
    assert added.is_revealed is False
    assert len(added.redemption_code) == 6


@pytest.mark.asyncio
async def test_fifth_scan_with_no_prize_pool_configured_creates_no_card(mocker):
    branch = make_branch()
    customer = make_customer(total_stamps_alltime=15)
    session = MagicMock()
    session.execute = AsyncMock(return_value=make_scalars_result([]))
    mocker.patch("app.services.loyalty_service.tenant_has_feature", AsyncMock(return_value=True))
    service = LoyaltyService(session=session)

    card_id, locked = await service._maybe_create_scratch_card(branch, customer)

    assert card_id is None
    assert locked is False
    session.add.assert_not_called()


# --- reveal_scratch_card ----------------------------------------------------


def make_card(**overrides) -> ScratchCard:
    defaults = {
        "tenant_id": TENANT_ID,
        "branch_id": BRANCH_ID,
        "customer_id": uuid.uuid4(),
        "prize_label": "Free Coffee",
        "redemption_code": "ABC123",
        "is_revealed": False,
        "revealed_at": None,
    }
    defaults.update(overrides)
    card = ScratchCard(**defaults)
    card.id = uuid.uuid4()
    return card


@pytest.mark.asyncio
async def test_reveal_at_or_above_threshold_returns_the_prize_and_code():
    customer = make_customer()
    card = make_card(customer_id=customer.id)
    session = make_session([card])
    service = LoyaltyService(session=session)

    response = await service.reveal_scratch_card(card.id, customer, scratched_percentage=0.7)

    assert response.prize_label == "Free Coffee"
    assert response.redemption_code == "ABC123"
    assert card.is_revealed is True
    assert card.revealed_at is not None


@pytest.mark.asyncio
async def test_reveal_below_threshold_returns_400_and_does_not_reveal():
    customer = make_customer()
    card = make_card(customer_id=customer.id)
    session = make_session([card])
    service = LoyaltyService(session=session)

    with pytest.raises(HTTPException) as exc_info:
        await service.reveal_scratch_card(card.id, customer, scratched_percentage=0.5)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "SCRATCH_CARD_INCOMPLETE"
    assert card.is_revealed is False


@pytest.mark.asyncio
async def test_reveal_already_revealed_card_returns_409():
    customer = make_customer()
    card = make_card(
        customer_id=customer.id, is_revealed=True, revealed_at=datetime.now(timezone.utc)
    )
    session = make_session([card])
    service = LoyaltyService(session=session)

    with pytest.raises(HTTPException) as exc_info:
        await service.reveal_scratch_card(card.id, customer, scratched_percentage=0.9)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "SCRATCH_CARD_ALREADY_REVEALED"


@pytest.mark.asyncio
async def test_reveal_missing_card_returns_404():
    customer = make_customer()
    session = make_session([None])
    service = LoyaltyService(session=session)

    with pytest.raises(HTTPException) as exc_info:
        await service.reveal_scratch_card(uuid.uuid4(), customer, scratched_percentage=0.9)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "SCRATCH_CARD_NOT_FOUND"


@pytest.mark.asyncio
async def test_reveal_another_customers_card_returns_404_not_403():
    """404-for-both, same shape as _get_owned_reward_program — never confirm
    to an attacker that a card id belongs to *someone*."""
    owner_customer = make_customer()
    other_customer = make_customer()
    card = make_card(customer_id=owner_customer.id)
    session = make_session([card])
    service = LoyaltyService(session=session)

    with pytest.raises(HTTPException) as exc_info:
        await service.reveal_scratch_card(card.id, other_customer, scratched_percentage=0.9)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "SCRATCH_CARD_NOT_FOUND"


# --- prize pool CRUD ---------------------------------------------------------


@pytest.mark.asyncio
async def test_create_prize_pool_entry():
    branch = make_branch()
    owner = User(tenant_id=TENANT_ID, role_id=uuid.uuid4())
    owner.id = uuid.uuid4()
    session = make_session([branch])
    service = LoyaltyService(session=session)

    response = await service.create_prize_pool_entry(
        PrizePoolCreateRequest(branch_id=BRANCH_ID, prize_label="Free Dessert"), owner
    )

    assert response.prize_label == "Free Dessert"
    assert response.branch_name == "Test Branch"
    assert response.is_active is True


@pytest.mark.asyncio
async def test_create_prize_pool_entry_unknown_branch_returns_404():
    owner = User(tenant_id=TENANT_ID, role_id=uuid.uuid4())
    owner.id = uuid.uuid4()
    session = make_session([None])
    service = LoyaltyService(session=session)

    with pytest.raises(HTTPException) as exc_info:
        await service.create_prize_pool_entry(
            PrizePoolCreateRequest(branch_id=uuid.uuid4(), prize_label="Free Dessert"), owner
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_prize_pool_entries():
    from app.db.models.loyalty import BranchPrizePool

    entry = BranchPrizePool(
        tenant_id=TENANT_ID, branch_id=BRANCH_ID, prize_label="Free Dessert", is_active=True
    )
    entry.id = uuid.uuid4()
    result = MagicMock()
    result.all.return_value = [(entry, "Test Branch")]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    service = LoyaltyService(session=session)

    entries = await service.list_prize_pool_entries()

    assert len(entries) == 1
    assert entries[0].prize_label == "Free Dessert"
    assert entries[0].branch_name == "Test Branch"
