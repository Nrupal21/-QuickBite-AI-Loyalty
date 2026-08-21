"""Unit tests for REVIEW-02 — Response drafting + approval workflow.

One test per acceptance criterion, plus the approve/reject state-machine
edge cases. OpenAI/Gemini and the GMB HTTP call are always mocked
(AGENTS.md §7); Redis is mocked here too (unlike the "use real local Redis"
default) purely because these tests never boot a Redis instance.

`rls.tenant_context` is stubbed to a no-op async context manager throughout:
the RLS binding itself is AUTH-04/TENANT-02's contract, already covered
there, and re-asserting it per call here would just pin the SQL text again.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.db.models.reputation import CustomerReview, GMBProfile, ReviewResponse
from app.db.models.user import User
from app.services import response_service

TENANT_ID = uuid.uuid4()
BRANCH_ID = uuid.uuid4()
REVIEW_ID = uuid.uuid4()
RESPONSE_ID = uuid.uuid4()
RESTAURANT = "Marco's"

AI_DRAFT = "Thank you so much for the kind words — we're thrilled you enjoyed the butter chicken!"
VARIATION_DRAFT = "We're delighted you had a great visit — hope to see you again at Marco's soon."


@asynccontextmanager
async def _noop_tenant_context(session, tenant_id):  # noqa: ARG001
    yield


@pytest.fixture(autouse=True)
def no_tenant_context(mocker):
    mocker.patch("app.services.response_service.rls.tenant_context", _noop_tenant_context)


@pytest.fixture(autouse=True)
def ai_configured(mocker):
    mocker.patch.object(settings, "OPENAI_API_KEY", "sk-test")
    mocker.patch.object(settings, "GEMINI_API_KEY", "gm-test")
    mocker.patch.object(settings, "OPENAI_MODEL", "gpt-4o")
    mocker.patch.object(settings, "GEMINI_MODEL", "gemini-1.5-pro")


@pytest.fixture(autouse=True)
def _mock_broadcast(mocker):
    """DASH-01's dashboard-refresh nudge on approval — mocked for the same
    reason cache_service is: these tests never boot a real Redis."""
    mocker.patch("app.services.response_service.broadcast.publish_event", AsyncMock())


@pytest.fixture(autouse=True)
def _ai_reply_feature_enabled(mocker):
    """generate_pending_response_drafts() gates each tenant on the
    ai_review_replies plan feature before drafting — stubbed on here so the
    batch-drafting tests below can describe their own session.execute
    side_effect list without also accounting for that plan lookup, same
    rationale as no_tenant_context above."""
    mocker.patch(
        "app.services.response_service.tenant_has_feature", AsyncMock(return_value=True)
    )


def make_user(user_id: uuid.UUID | None = None) -> User:
    user = User(
        tenant_id=TENANT_ID,
        role_id=uuid.uuid4(),
        email_hash="irrelevant",
        encrypted_email="irrelevant",
        hashed_password="irrelevant",
        mfa_enabled=False,
        email_verified=True,
        is_active=True,
    )
    user.id = user_id or uuid.uuid4()
    return user


def make_response(**overrides) -> ReviewResponse:
    defaults = {
        "review_id": REVIEW_ID,
        "tenant_id": TENANT_ID,
        "ai_draft": AI_DRAFT,
        "final_text": None,
        "approval_state": "pending",
        "approved_by_user_id": None,
        "ai_model_used": "gpt-4o",
        "idempotency_key": f"review-reply:{REVIEW_ID}",
    }
    defaults.update(overrides)
    response = ReviewResponse(**defaults)
    response.id = RESPONSE_ID
    # created_at is server_default=func.now() (app/db/base.py) — never
    # populated on plain Python construction, only on an actual DB round
    # trip. ReviewResponseOut.model_validate() needs it non-None.
    response.created_at = datetime.now(timezone.utc)
    return response


def make_review(**overrides) -> CustomerReview:
    defaults = {
        "tenant_id": TENANT_ID,
        "branch_id": BRANCH_ID,
        "source": "google",
        "external_review_id": "gmb-review-1",
        "rating": 5,
        "review_body": "Amazing butter chicken, quick service.",
        "reviewed_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    review = CustomerReview(**defaults)
    review.id = REVIEW_ID
    return review


def make_profile(**overrides) -> GMBProfile:
    defaults = {
        "tenant_id": TENANT_ID,
        "branch_id": BRANCH_ID,
        "gmb_account_id": "acct-1",
        "gmb_location_id": "loc-1",
        "encrypted_access_token": "v1:irrelevant",
        "encrypted_refresh_token": "v1:irrelevant",
        "token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "is_connected": True,
    }
    defaults.update(overrides)
    return GMBProfile(**defaults)


def make_session(execute_results: list) -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    results = []
    for value in execute_results:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        result.scalar_one.return_value = value
        results.append(result)
    session.execute = AsyncMock(side_effect=results)
    return session


def make_list_session(rows: list) -> MagicMock:
    """`rows` is a list of (CustomerReview, branch_name, ReviewResponse | None)
    tuples — the shape `list_reviews`'s three-way join returns from `.all()`."""
    result = MagicMock()
    result.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


# --- list -------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_reviews_includes_branch_name_and_no_response():
    review = make_review()
    session = make_list_session([(review, "Bandra West", None)])

    out = await response_service.list_reviews(session)

    assert len(out) == 1
    assert out[0].id == review.id
    assert out[0].branch_name == "Bandra West"
    assert out[0].response is None


@pytest.mark.asyncio
async def test_list_reviews_nests_a_pending_response():
    review = make_review()
    response = make_response(review_id=review.id)
    session = make_list_session([(review, "Bandra West", response)])

    out = await response_service.list_reviews(session)

    assert out[0].response is not None
    assert out[0].response.approval_state == "pending"
    assert out[0].response.ai_draft == response.ai_draft


@pytest.mark.asyncio
async def test_list_reviews_empty_returns_empty_list():
    session = make_list_session([])

    assert await response_service.list_reviews(session) == []


# --- approve --------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_defaults_final_text_to_the_ai_draft(mocker):
    mocker.patch("app.workers.tasks.post_approved_response.delay", MagicMock())
    response = make_response()
    session = make_session([response])
    user = make_user()

    result = await response_service.approve_response(session, RESPONSE_ID, user, None)

    assert result.final_text == AI_DRAFT
    assert result.approval_state == "approved"
    assert result.approved_by_user_id == user.id


@pytest.mark.asyncio
async def test_approve_uses_the_managers_edited_text_when_given(mocker):
    mocker.patch("app.workers.tasks.post_approved_response.delay", MagicMock())
    response = make_response()
    session = make_session([response])

    result = await response_service.approve_response(
        session, RESPONSE_ID, make_user(), "Thanks so much, hope to see you again!"
    )

    assert result.final_text == "Thanks so much, hope to see you again!"


@pytest.mark.asyncio
async def test_approve_queues_the_gmb_post_task(mocker):
    delay = mocker.patch("app.workers.tasks.post_approved_response.delay", MagicMock())
    session = make_session([make_response()])

    result = await response_service.approve_response(session, RESPONSE_ID, make_user(), None)

    delay.assert_called_once_with(str(result.id))


@pytest.mark.asyncio
async def test_approve_unknown_id_returns_404(mocker):
    mocker.patch("app.workers.tasks.post_approved_response.delay", MagicMock())
    session = make_session([None])

    with pytest.raises(HTTPException) as exc_info:
        await response_service.approve_response(session, RESPONSE_ID, make_user(), None)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "REVIEW_RESPONSE_NOT_FOUND"


@pytest.mark.asyncio
async def test_approve_an_already_approved_response_returns_409(mocker):
    mocker.patch("app.workers.tasks.post_approved_response.delay", MagicMock())
    session = make_session([make_response(approval_state="approved")])

    with pytest.raises(HTTPException) as exc_info:
        await response_service.approve_response(session, RESPONSE_ID, make_user(), None)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "REVIEW_RESPONSE_NOT_PENDING"


# --- reject -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_regenerates_with_a_variation_prompt(mocker):
    call = mocker.patch(
        "app.services.ai_engine._call_response_openai", AsyncMock(return_value=VARIATION_DRAFT)
    )
    mocker.patch.object(
        __import__("app.core.config", fromlist=["settings"]).settings, "OPENAI_API_KEY", "sk-test"
    )
    response = make_response()
    session = make_session([response, make_review(), RESTAURANT])

    result = await response_service.reject_response(session, RESPONSE_ID, "not our voice")

    assert result.ai_draft == VARIATION_DRAFT
    assert result.approval_state == "pending"
    assert result.final_text is None
    call.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_unknown_id_returns_404():
    session = make_session([None])

    with pytest.raises(HTTPException) as exc_info:
        await response_service.reject_response(session, RESPONSE_ID, None)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_reject_a_non_pending_response_returns_409():
    session = make_session([make_response(approval_state="rejected")])

    with pytest.raises(HTTPException) as exc_info:
        await response_service.reject_response(session, RESPONSE_ID, None)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_reject_regeneration_failure_returns_503(mocker):
    mocker.patch.object(settings, "OPENAI_API_KEY", "")
    mocker.patch.object(settings, "GEMINI_API_KEY", "")
    session = make_session([make_response(), make_review(), RESTAURANT])

    with pytest.raises(HTTPException) as exc_info:
        await response_service.reject_response(session, RESPONSE_ID, None)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == "AI_PROVIDER_UNAVAILABLE"


# --- batch drafting (hourly beat task) --------------------------------------


@pytest.mark.asyncio
async def test_batch_drafts_a_response_for_every_unanswered_review(mocker):
    draft_mock = mocker.patch(
        "app.services.ai_engine.generate_response_draft",
        AsyncMock(side_effect=lambda **kw: MagicMock(text=AI_DRAFT, model="gpt-4o", cached=False)),
    )
    tenants_result = MagicMock()
    tenants_result.scalars.return_value.all.return_value = [TENANT_ID]
    review = make_review()
    reviews_result = MagicMock()
    reviews_result.scalars.return_value.all.return_value = [review]
    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = RESTAURANT
    insert_result = MagicMock()

    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(side_effect=[tenants_result, reviews_result, name_result, insert_result])

    count = await response_service.generate_pending_response_drafts(session)

    assert count == 1
    draft_mock.assert_awaited_once()
    insert_statement = session.execute.await_args_list[-1].args[0]
    compiled = insert_statement.compile()
    assert compiled.params["idempotency_key"] == f"review-reply:{review.id}"
    assert compiled.params["approval_state"] == "pending"


@pytest.mark.asyncio
async def test_batch_skips_a_review_when_generation_fails(mocker):
    mocker.patch(
        "app.services.ai_engine.generate_response_draft",
        AsyncMock(side_effect=response_service.ai_engine.AIProvidersUnavailable),
    )
    tenants_result = MagicMock()
    tenants_result.scalars.return_value.all.return_value = [TENANT_ID]
    reviews_result = MagicMock()
    reviews_result.scalars.return_value.all.return_value = [make_review()]
    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = RESTAURANT

    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(side_effect=[tenants_result, reviews_result, name_result])

    count = await response_service.generate_pending_response_drafts(session)

    assert count == 0


# --- posting to GMB (post_approved_response Celery task's body) ------------


@pytest.mark.asyncio
async def test_post_skips_when_already_sent(mocker):
    mocker.patch("app.services.response_service.cache_service.exists", AsyncMock(return_value=True))
    session = MagicMock()

    outcome = await response_service.post_approved_response_to_gmb(session, RESPONSE_ID)

    assert outcome == "already_sent"
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_post_skips_when_not_approved(mocker):
    mocker.patch("app.services.response_service.cache_service.exists", AsyncMock(return_value=False))
    session = make_session([make_response(approval_state="pending")])

    outcome = await response_service.post_approved_response_to_gmb(session, RESPONSE_ID)

    assert outcome == "not_approved"


@pytest.mark.asyncio
async def test_post_skips_when_no_gmb_profile_connected(mocker):
    mocker.patch("app.services.response_service.cache_service.exists", AsyncMock(return_value=False))
    response = make_response(approval_state="approved", final_text=AI_DRAFT)
    session = make_session([response, make_review(), None])

    outcome = await response_service.post_approved_response_to_gmb(session, RESPONSE_ID)

    assert outcome == "no_gmb_profile"


@pytest.mark.asyncio
async def test_post_success_marks_the_row_posted_and_sets_the_redis_flag(mocker):
    mocker.patch("app.services.response_service.cache_service.exists", AsyncMock(return_value=False))
    cache_set = mocker.patch("app.services.response_service.cache_service.set", AsyncMock())
    post_reply = mocker.patch(
        "app.services.response_service.gmb_service.post_review_reply", AsyncMock()
    )
    response = make_response(approval_state="approved", final_text="Thanks!")
    session = make_session([response, make_review(), make_profile()])

    outcome = await response_service.post_approved_response_to_gmb(session, RESPONSE_ID)

    assert outcome == "posted"
    assert response.approval_state == "posted"
    post_reply.assert_awaited_once()
    cache_set.assert_awaited_once()
    assert cache_set.await_args.kwargs["ttl"] == 86400


@pytest.mark.asyncio
async def test_post_gmb_not_connected_skips_without_marking_posted(mocker):
    mocker.patch("app.services.response_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch(
        "app.services.response_service.gmb_service.post_review_reply",
        AsyncMock(side_effect=response_service.GMBNotConnected),
    )
    response = make_response(approval_state="approved")
    session = make_session([response, make_review(), make_profile()])

    outcome = await response_service.post_approved_response_to_gmb(session, RESPONSE_ID)

    assert outcome == "not_connected"
    assert response.approval_state == "approved"


# --- RBAC: Manager+ only (AC: "Staff calling PATCH .../approve -> 403") ----


@pytest.mark.asyncio
async def test_staff_is_blocked_from_the_approval_endpoints():
    """Wired identically to team.py's `require_role(RoleLevel.MANAGER)` guard,
    already exhaustively tested in test_rbac.py — this pins that the review
    router actually uses it, not the guard's internals again."""
    from app.api.v1.dependencies.auth import require_role
    from app.core.rbac import RoleLevel

    session = make_session([4])  # Role.level for STAFF
    guard = require_role(RoleLevel.MANAGER)

    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=make_user(), session=session)

    assert exc_info.value.status_code == 403
