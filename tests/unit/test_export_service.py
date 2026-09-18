"""Unit tests for NICE-04's export orchestration and report builders.

External services (R2 upload, email) and the DB session are mocked per
AGENTS.md's testing rules — this exercises export_service's own logic
(idempotency, RLS binding, report content), not storage_service or
messaging_service, which have their own test files.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.schemas.dashboard import DashboardStatsResponse, LoyaltyAnalyticsResponse
from app.services import export_service

TENANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def make_tenant() -> Tenant:
    tenant = Tenant(subdomain="marcos", name="Marco's Pizzeria")
    tenant.id = TENANT_ID
    return tenant


def make_user() -> User:
    user = User(tenant_id=TENANT_ID, role_id=uuid.uuid4(), encrypted_email="encrypted-owner-email")
    user.id = USER_ID
    return user


def make_stats(**overrides) -> DashboardStatsResponse:
    defaults = {
        "avg_rating": 4.5,
        "review_count": 12,
        "sentiment_trend": [],
        "pending_approvals": 0,
        "loyalty_scans_today": 3,
        "fraud_alert_count": 0,
        "can_approve": False,
    }
    defaults.update(overrides)
    return DashboardStatsResponse(**defaults)


def make_analytics(**overrides) -> LoyaltyAnalyticsResponse:
    defaults = {
        "total_stamps_month": 42,
        "active_loyalty_customers": 8,
        "heatmap": [],
        "top_customers": [],
        "branch_comparison": [],
        "redemption_rate": 0.25,
        "fraud_log": [],
    }
    defaults.update(overrides)
    return LoyaltyAnalyticsResponse(**defaults)


def make_session(tenant: Tenant, user: User) -> MagicMock:
    tenant_result = MagicMock()
    tenant_result.scalar_one.return_value = tenant
    user_result = MagicMock()
    user_result.scalar_one.return_value = user
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[tenant_result, user_result])
    return session


@pytest.fixture(autouse=True)
def mocked_collaborators(mocker):
    mocker.patch("app.services.export_service.rls.set_tenant_context", AsyncMock())
    mocker.patch(
        "app.services.export_service.dashboard_service.get_loyalty_analytics",
        AsyncMock(return_value=make_analytics()),
    )
    mocker.patch(
        "app.services.export_service.dashboard_service.get_stats",
        AsyncMock(return_value=make_stats()),
    )
    mocker.patch("app.services.export_service.decrypt_pii", lambda _: "owner@marcos.in")
    return mocker


@pytest.mark.asyncio
async def test_already_sent_export_is_skipped(mocker):
    mocker.patch("app.services.export_service.cache_service.exists", AsyncMock(return_value=True))
    session = MagicMock()
    session.execute = AsyncMock()

    await export_service.generate_and_email_export(session, TENANT_ID, USER_ID, "export-1", "csv")

    session.execute.assert_not_awaited()  # short-circuited before any query


@pytest.mark.asyncio
async def test_csv_export_uploads_and_emails_the_owner(mocker):
    mocker.patch("app.services.export_service.cache_service.exists", AsyncMock(return_value=False))
    cache_set = mocker.patch("app.services.export_service.cache_service.set", AsyncMock())
    session = make_session(make_tenant(), make_user())
    upload = mocker.patch(
        "app.services.export_service.storage_service.upload_and_presign",
        return_value="https://r2.example/signed",
    )
    send_email = mocker.patch(
        "app.services.export_service.messaging_service.send_export_ready_email", AsyncMock(return_value=True)
    )

    await export_service.generate_and_email_export(session, TENANT_ID, USER_ID, "export-2", "csv")

    upload.assert_called_once()
    assert upload.call_args.args[2] == "text/csv"
    send_email.assert_awaited_once()
    assert send_email.await_args.kwargs["download_url"] == "https://r2.example/signed"
    assert send_email.await_args.kwargs["export_format"] == "CSV"
    cache_set.assert_awaited_once()
    assert cache_set.await_args.args[0] == "export:export-2:sent"


@pytest.mark.asyncio
async def test_pdf_export_uses_pdf_content_type(mocker):
    mocker.patch("app.services.export_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.services.export_service.cache_service.set", AsyncMock())
    session = make_session(make_tenant(), make_user())
    upload = mocker.patch(
        "app.services.export_service.storage_service.upload_and_presign",
        return_value="https://r2.example/signed.pdf",
    )
    mocker.patch(
        "app.services.export_service.messaging_service.send_export_ready_email", AsyncMock(return_value=True)
    )

    await export_service.generate_and_email_export(session, TENANT_ID, USER_ID, "export-3", "pdf")

    assert upload.call_args.args[2] == "application/pdf"
    # A real PDF starts with the %PDF- magic bytes.
    assert upload.call_args.args[1].startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_rls_context_bound_before_any_query(mocker):
    set_tenant = mocker.patch("app.services.export_service.rls.set_tenant_context", AsyncMock())
    mocker.patch("app.services.export_service.cache_service.exists", AsyncMock(return_value=False))
    mocker.patch("app.services.export_service.cache_service.set", AsyncMock())
    session = make_session(make_tenant(), make_user())
    mocker.patch("app.services.export_service.storage_service.upload_and_presign", return_value="url")
    mocker.patch(
        "app.services.export_service.messaging_service.send_export_ready_email", AsyncMock(return_value=True)
    )

    await export_service.generate_and_email_export(session, TENANT_ID, USER_ID, "export-4", "csv")

    set_tenant.assert_awaited_once_with(session, TENANT_ID)


def test_build_csv_contains_summary_and_branch_sections():
    analytics = make_analytics(
        branch_comparison=[
            {"branch_id": uuid.uuid4(), "branch_name": "Bandra", "scan_count": 10}
        ]
    )
    stats = make_stats()

    csv_bytes = export_service._build_csv(analytics, stats)
    text = csv_bytes.decode("utf-8")

    assert "Summary" in text
    assert "Top Customers" in text
    assert "Branch Comparison" in text
    assert "Bandra" in text


def test_build_pdf_returns_valid_pdf_bytes_with_no_branch_activity():
    """Empty branch_comparison must not crash the table builder — this is
    the regression case for the `or` operator-precedence bug in an earlier
    draft (`[header] + [] or [fallback]` never took the fallback branch)."""
    analytics = make_analytics(branch_comparison=[])
    stats = make_stats(avg_rating=None)

    pdf_bytes = export_service._build_pdf("Marco's Pizzeria", analytics, stats)

    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 100
