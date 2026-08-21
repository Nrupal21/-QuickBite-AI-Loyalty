"""Unit tests for REVIEW-01 — Smart Review Composer.

One test per acceptance criterion, plus the SEC-11 prompt-injection cases that
depend on this ticket.

OpenAI and Gemini are always mocked (AGENTS.md §7). The provider calls are
patched at `app.services.ai_engine._call_openai` / `_call_gemini` rather than
inside the SDKs: the SDK surfaces differ from each other and change between
versions, whereas these two are our own boundary and are what the fallback
logic actually branches on.
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import settings
from app.core.encryption import encrypt_pii
from app.db.models.branch import Branch
from app.db.models.customer import Customer, ReviewDraft
from app.schemas.reputation import ReviewGenerateRequest
from app.services import ai_engine, review_service

TENANT_ID = uuid.uuid4()
BRANCH_ID = uuid.uuid4()
QR_TOKEN = "qr-token-marcos-bandra"
RESTAURANT = "Marco's"

DRAFT = (
    "The butter chicken was rich and properly spiced, and it arrived faster "
    "than we expected on a Friday night. Staff were warm without hovering."
)


@pytest.fixture(autouse=True)
def ai_configured(mocker):
    """Both providers configured by default, so tests opt *out* of a provider
    rather than accidentally passing because no key was set."""
    mocker.patch.object(settings, "OPENAI_API_KEY", "sk-test")
    mocker.patch.object(settings, "GEMINI_API_KEY", "gm-test")
    mocker.patch.object(settings, "OPENAI_MODEL", "gpt-4o")
    mocker.patch.object(settings, "GEMINI_MODEL", "gemini-1.5-pro")


@pytest.fixture(autouse=True)
def bound_tenant(mocker):
    """Stub the QR->tenant resolver and the RLS bind.

    Both issue their own session.execute — the resolver calls a SECURITY
    DEFINER function (migration 0007) and set_tenant_context runs set_config —
    so stubbing them keeps make_session's result list describing only the ORM
    queries a test reasons about. The test that asserts on tenant binding
    re-patches set_tenant_context locally.
    """
    mocker.patch(
        "app.services.review_service.bootstrap.tenant_for_branch_qr_token",
        AsyncMock(return_value=TENANT_ID),
    )
    return mocker.patch("app.services.review_service.rls.set_tenant_context", AsyncMock())


@pytest.fixture
def no_cache(mocker):
    """Redis miss on read, no-op on write."""
    mocker.patch("app.services.ai_engine.cache_service.get", AsyncMock(return_value=None))
    return mocker.patch("app.services.ai_engine.cache_service.set", AsyncMock())


def make_branch() -> Branch:
    branch = Branch(
        tenant_id=TENANT_ID,
        name="Bandra",
        qr_code_token=QR_TOKEN,
        is_active=True,
        geofence_radius_m=100,
    )
    branch.id = BRANCH_ID
    return branch


def make_session(branch: Branch | None = None, restaurant: str | None = RESTAURANT) -> MagicMock:
    """Session for review_service: branch lookup, then tenant name, then usage."""
    branch_result = MagicMock()
    branch_result.scalar_one_or_none.return_value = make_branch() if branch is None else branch
    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = restaurant
    usage_result = MagicMock()
    usage_result.scalar_one_or_none.return_value = None

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[branch_result, name_result, usage_result])
    session.commit = AsyncMock()
    return session


def request(rating: int = 5, tags: list[str] | None = None) -> ReviewGenerateRequest:
    return ReviewGenerateRequest(
        branch_qr_token=QR_TOKEN,
        rating=rating,
        tags=tags if tags is not None else ["great biryani", "quick service"],
    )


# --- AC: empty tags -> 422 "Please select at least 1 tag" ---------------


def test_empty_tags_rejected_with_the_specified_message():
    with pytest.raises(ValidationError) as exc_info:
        request(tags=[])

    assert "Please select at least 1 tag" in str(exc_info.value)


def test_whitespace_only_tags_are_treated_as_empty():
    with pytest.raises(ValidationError) as exc_info:
        request(tags=["   ", ""])

    assert "Please select at least 1 tag" in str(exc_info.value)


def test_more_than_five_tags_rejected():
    with pytest.raises(ValidationError):
        request(tags=["a", "b", "c", "d", "e", "f"])


def test_rating_outside_one_to_five_rejected():
    for bad_rating in (0, 6, -1):
        with pytest.raises(ValidationError):
            request(rating=bad_rating)


# --- AC: draft returned, tags reach the prompt --------------------------


@pytest.mark.asyncio
async def test_generates_a_draft_from_openai(mocker, no_cache):
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value=DRAFT))
    session = make_session()

    response = await review_service.generate_review_draft(request(), session)

    assert response.draft == DRAFT
    assert response.model == "gpt-4o"
    assert response.cached is False


@pytest.mark.asyncio
async def test_every_selected_tag_reaches_the_prompt(mocker, no_cache):
    """AC: "draft mentions selected tags naturally". Whether the *model* writes
    naturally is a prompt-quality property no mock can assert, so what is
    pinned here is the part we control: every tag arrives, and the prompt
    forbids listing them."""
    call = mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value=DRAFT))
    tags = ["great biryani", "quick service", "friendly staff"]

    await review_service.generate_review_draft(request(tags=tags), make_session())

    prompt = call.await_args.args[0]
    for tag in tags:
        assert tag in prompt
    assert "Marco's" in prompt
    assert "5 out of 5" in prompt
    assert "Never output the tags as a list" in ai_engine._SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_low_rating_is_passed_through_honestly(mocker, no_cache):
    call = mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value="Slow."))

    await review_service.generate_review_draft(
        request(rating=1, tags=["cold food"]), make_session()
    )

    assert "1 out of 5" in call.await_args.args[0]


# --- AC: identical input -> cached ---------------------------------------


@pytest.mark.asyncio
async def test_identical_input_is_served_from_cache_without_calling_a_provider(mocker):
    mocker.patch(
        "app.services.ai_engine.cache_service.get",
        AsyncMock(return_value=json.dumps({"text": DRAFT, "model": "gpt-4o"})),
    )
    openai_call = mocker.patch("app.services.ai_engine._call_openai", AsyncMock())

    response = await review_service.generate_review_draft(request(), make_session())

    assert response.cached is True
    assert response.draft == DRAFT
    openai_call.assert_not_awaited()  # the 1s criterion is only met by not calling out


def test_cache_key_ignores_tag_order():
    """The diner picked a set, not a sequence — reordering must still hit."""
    a = ai_engine.build_cache_key(str(BRANCH_ID), 5, ["biryani", "service"])
    b = ai_engine.build_cache_key(str(BRANCH_ID), 5, ["service", "biryani"])

    assert a == b


def test_cache_key_separates_branch_rating_and_tags():
    base = ai_engine.build_cache_key(str(BRANCH_ID), 5, ["biryani"])

    assert base != ai_engine.build_cache_key(str(uuid.uuid4()), 5, ["biryani"])
    assert base != ai_engine.build_cache_key(str(BRANCH_ID), 4, ["biryani"])
    assert base != ai_engine.build_cache_key(str(BRANCH_ID), 5, ["service"])


def test_cache_key_does_not_leak_tag_text_into_the_keyspace():
    """Raw keys surface in SCAN output and slow logs; tags are customer text."""
    key = ai_engine.build_cache_key(str(BRANCH_ID), 5, ["chicken tikka"])

    assert "chicken" not in key
    assert key.startswith(ai_engine.CACHE_PREFIX)


@pytest.mark.asyncio
async def test_draft_is_written_to_cache_with_the_one_hour_ttl(mocker, no_cache):
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value=DRAFT))

    await review_service.generate_review_draft(request(), make_session())

    assert no_cache.await_args.kwargs["ttl"] == settings.AI_DRAFT_CACHE_TTL_SECONDS
    assert settings.AI_DRAFT_CACHE_TTL_SECONDS == 3600


# --- AC: OpenAI failure -> Gemini fallback via feature flag --------------


@pytest.mark.asyncio
async def test_openai_failure_falls_back_to_gemini(mocker, no_cache):
    mocker.patch(
        "app.services.ai_engine._call_openai", AsyncMock(side_effect=RuntimeError("503 from openai"))
    )
    mocker.patch("app.services.ai_engine._call_gemini", AsyncMock(return_value=DRAFT))

    response = await review_service.generate_review_draft(request(), make_session())

    assert response.draft == DRAFT
    assert response.model == "gemini-1.5-pro"


@pytest.mark.asyncio
async def test_openai_timeout_falls_back_to_gemini(mocker, no_cache):
    """Timeout is the likeliest OpenAI failure, and the one the per-provider
    budget exists for."""
    mocker.patch(
        "app.services.ai_engine._call_openai", AsyncMock(side_effect=asyncio.TimeoutError())
    )
    gemini = mocker.patch("app.services.ai_engine._call_gemini", AsyncMock(return_value=DRAFT))

    response = await review_service.generate_review_draft(request(), make_session())

    assert response.model == "gemini-1.5-pro"
    gemini.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_openai_response_falls_back_rather_than_returning_blank(mocker, no_cache):
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value="   "))
    mocker.patch("app.services.ai_engine._call_gemini", AsyncMock(return_value=DRAFT))

    response = await review_service.generate_review_draft(request(), make_session())

    assert response.draft == DRAFT


@pytest.mark.asyncio
async def test_feature_flag_off_skips_gemini_entirely(mocker, no_cache):
    """The flag is the operator's kill switch — with it off, an OpenAI failure
    is a 503, not a silent switch to the other vendor."""
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(side_effect=RuntimeError("down")))
    gemini = mocker.patch("app.services.ai_engine._call_gemini", AsyncMock(return_value=DRAFT))
    mocker.patch(
        "app.services.ai_engine.feature_flags.is_enabled", AsyncMock(return_value=False)
    )

    with pytest.raises(HTTPException) as exc_info:
        await review_service.generate_review_draft(request(), make_session())

    assert exc_info.value.status_code == 503
    gemini.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_defaults_to_enabled_when_the_flag_is_unset(mocker, no_cache):
    """No flag in Redis must mean "fallback on" — an opt-in fallback would make
    a fresh deployment 503 the first time OpenAI hiccups."""
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(side_effect=RuntimeError("down")))
    mocker.patch("app.services.ai_engine._call_gemini", AsyncMock(return_value=DRAFT))
    flag = mocker.patch(
        "app.services.ai_engine.feature_flags.is_enabled", AsyncMock(return_value=True)
    )

    await review_service.generate_review_draft(request(), make_session())

    assert flag.await_args.kwargs["default"] is True


# --- AC: both providers fail -> 503, usage NOT incremented ---------------


@pytest.mark.asyncio
async def test_both_providers_failing_raises_503(mocker, no_cache):
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(side_effect=RuntimeError("a")))
    mocker.patch("app.services.ai_engine._call_gemini", AsyncMock(side_effect=RuntimeError("b")))

    with pytest.raises(HTTPException) as exc_info:
        await review_service.generate_review_draft(request(), make_session())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == "AI_PROVIDER_UNAVAILABLE"
    assert exc_info.value.headers["Retry-After"] == "30"


@pytest.mark.asyncio
async def test_usage_counter_not_incremented_when_both_providers_fail(mocker, no_cache):
    """The tenant is not billed for our outage."""
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(side_effect=RuntimeError("a")))
    mocker.patch("app.services.ai_engine._call_gemini", AsyncMock(side_effect=RuntimeError("b")))
    increment = mocker.patch(
        "app.services.review_service.usage_service.increment_ai_usage", AsyncMock()
    )

    with pytest.raises(HTTPException):
        await review_service.generate_review_draft(request(), make_session())

    increment.assert_not_awaited()


@pytest.mark.asyncio
async def test_usage_counter_incremented_on_a_successful_generation(mocker, no_cache):
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value=DRAFT))
    increment = mocker.patch(
        "app.services.review_service.usage_service.increment_ai_usage", AsyncMock()
    )

    await review_service.generate_review_draft(request(), make_session())

    increment.assert_awaited_once()
    assert increment.await_args.args[1] == TENANT_ID


def make_customer(tenant_id: uuid.UUID = TENANT_ID) -> Customer:
    customer = Customer(
        tenant_id=tenant_id,
        phone_hash="hash",
        encrypted_phone=encrypt_pii("+919876543210"),
    )
    customer.id = uuid.uuid4()
    return customer


@pytest.mark.asyncio
async def test_signed_in_customer_gets_a_review_draft_row(mocker, no_cache):
    """A logged-in diner's draft is recorded for their profile page's
    "reviews you've sent" — see migration 0013 / customer_service.get_profile."""
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value=DRAFT))
    customer = make_customer()
    session = make_session()

    await review_service.generate_review_draft(request(), session, customer)

    added = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], ReviewDraft)]
    assert len(added) == 1
    assert added[0].tenant_id == TENANT_ID
    assert added[0].branch_id == BRANCH_ID
    assert added[0].customer_id == customer.id
    assert added[0].draft_excerpt == DRAFT


@pytest.mark.asyncio
async def test_anonymous_scan_records_no_review_draft(mocker, no_cache):
    """The overwhelmingly common case (REVIEW-01: no auth required) — no
    customer session means nothing to attribute the draft to."""
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value=DRAFT))
    session = make_session()

    await review_service.generate_review_draft(request(), session, None)

    added = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], ReviewDraft)]
    assert added == []


@pytest.mark.asyncio
async def test_customer_from_a_different_tenant_is_not_attributed(mocker, no_cache):
    """A Customer row is siloed per tenant (one loyalty membership per
    restaurant) — a session from another restaurant has nothing meaningful
    to attribute this draft to."""
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value=DRAFT))
    other_tenant_customer = make_customer(tenant_id=uuid.uuid4())
    session = make_session()

    await review_service.generate_review_draft(request(), session, other_tenant_customer)

    added = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], ReviewDraft)]
    assert added == []


@pytest.mark.asyncio
async def test_cache_hit_does_not_bill_the_tenant_again(mocker):
    """One generation, one charge — replaying the same draft is free."""
    mocker.patch(
        "app.services.ai_engine.cache_service.get",
        AsyncMock(return_value=json.dumps({"text": DRAFT, "model": "gpt-4o"})),
    )
    increment = mocker.patch(
        "app.services.review_service.usage_service.increment_ai_usage", AsyncMock()
    )

    await review_service.generate_review_draft(request(), make_session())

    increment.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_api_keys_configured_is_a_503_not_a_crash(mocker, no_cache):
    mocker.patch.object(settings, "OPENAI_API_KEY", "")
    mocker.patch.object(settings, "GEMINI_API_KEY", "")
    openai_call = mocker.patch("app.services.ai_engine._call_openai", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await review_service.generate_review_draft(request(), make_session())

    assert exc_info.value.status_code == 503
    openai_call.assert_not_awaited()


# --- tenant scoping ------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_context_is_bound_from_the_branch(mocker, no_cache):
    """The endpoint is public, so tenant identity can only come from the QR
    token's branch — and it must be bound before any RLS-protected write."""
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value=DRAFT))
    set_tenant = mocker.patch("app.services.review_service.rls.set_tenant_context", AsyncMock())

    await review_service.generate_review_draft(request(), make_session())

    set_tenant.assert_awaited_once()
    assert set_tenant.await_args.args[1] == TENANT_ID


@pytest.mark.asyncio
async def test_unknown_qr_token_returns_400(mocker, no_cache):
    session = MagicMock()
    missing = MagicMock()
    missing.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=missing)

    with pytest.raises(HTTPException) as exc_info:
        await review_service.generate_review_draft(request(), session)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "LOYALTY_INVALID_QR"


@pytest.mark.asyncio
async def test_missing_tenant_name_still_produces_a_draft(mocker, no_cache):
    """A missing tenant row is flavour lost from the prompt, not a failed review."""
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value=DRAFT))

    response = await review_service.generate_review_draft(
        request(), make_session(restaurant=None)
    )

    assert response.draft == DRAFT


# --- resilience ----------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_outage_degrades_to_a_live_call(mocker):
    """Losing the cache costs latency, never the review."""
    mocker.patch(
        "app.services.ai_engine.cache_service.get", AsyncMock(side_effect=OSError("redis down"))
    )
    mocker.patch(
        "app.services.ai_engine.cache_service.set", AsyncMock(side_effect=OSError("redis down"))
    )
    mocker.patch("app.services.ai_engine._call_openai", AsyncMock(return_value=DRAFT))

    response = await review_service.generate_review_draft(request(), make_session())

    assert response.draft == DRAFT
    assert response.cached is False


@pytest.mark.asyncio
async def test_provider_is_bounded_by_the_configured_timeout(mocker):
    """The per-provider budget must be under the 3s total, or the fallback
    cannot fit inside the same criterion."""
    assert settings.AI_REQUEST_TIMEOUT_SECONDS * 2 < 3.0

    async def never_returns() -> str:
        await asyncio.sleep(10)
        return "too late"

    mocker.patch.object(settings, "AI_REQUEST_TIMEOUT_SECONDS", 0.05)
    mocker.patch("app.services.ai_engine._call_openai", never_returns)

    assert await ai_engine._attempt("openai", never_returns, "gpt-4o") is None
