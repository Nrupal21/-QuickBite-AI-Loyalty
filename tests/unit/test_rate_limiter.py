"""Unit tests for the tracked rate-limit-exceeded handler (ADMIN-01 monitors)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.principal import AuthProvider, Principal, SubjectType
from app.core.rate_limiter import rate_limit_exceeded_handler_with_tracking
from app.db.models.user import User


def make_request_with_principal(tenant_id, has_principal=True):
    request = MagicMock()
    request.state = MagicMock()
    if has_principal:
        user = User(tenant_id=tenant_id, role_id=uuid.uuid4(), is_active=True)
        user.id = uuid.uuid4()
        request.state.principal = Principal(
            subject_type=SubjectType.USER,
            tenant_id=tenant_id,
            auth_provider=AuthProvider.LOCAL,
            provider_subject=str(user.id),
            claims={},
            user=user,
        )
    else:
        del request.state.principal  # simulate no principal ever resolved
    return request


@pytest.mark.asyncio
async def test_tracks_429_when_principal_present(mocker):
    mocker.patch(
        "app.core.rate_limiter._rate_limit_exceeded_handler",
        MagicMock(return_value="the-response"),
    )
    incr = mocker.patch("app.core.rate_limiter.cache_service.incr", AsyncMock(return_value=1))
    tenant_id = uuid.uuid4()
    request = make_request_with_principal(tenant_id)

    response = await rate_limit_exceeded_handler_with_tracking(request, MagicMock())

    assert response == "the-response"
    incr.assert_awaited_once()
    assert incr.await_args.args[0].startswith(f"api_429:{tenant_id}:")


@pytest.mark.asyncio
async def test_skips_tracking_when_no_principal_resolved_yet(mocker):
    """A pre-auth rate limit (e.g. OTP request, login) has no tenant to
    attribute the 429 to — must not raise, just skip counting."""
    mocker.patch(
        "app.core.rate_limiter._rate_limit_exceeded_handler",
        MagicMock(return_value="the-response"),
    )
    incr = mocker.patch("app.core.rate_limiter.cache_service.incr", AsyncMock())
    request = make_request_with_principal(None, has_principal=False)

    response = await rate_limit_exceeded_handler_with_tracking(request, MagicMock())

    assert response == "the-response"
    incr.assert_not_awaited()
