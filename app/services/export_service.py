"""QuickBite — Analytics export: CSV (Pro+) and PDF (Enterprise) (NICE-04).

Reuses DASH-02's `dashboard_service.get_loyalty_analytics` and DASH-01's
`get_stats` as the data source rather than re-deriving report figures —
same numbers a Manager already sees on the dashboard, just exported.

Runs inside a Celery task (`app.workers.tasks.generate_export`), not the
request handler: PDF rendering and an R2 round trip are exactly the kind of
work Doc 2's Celery rule exists for. `generate_and_email_export` is the
thin-wrapper target, same shape as `response_service.generate_pending_response_drafts`
being wrapped by `batch_generate_ai_responses`.
"""

import csv
import io
import uuid
from datetime import UTC, datetime

import structlog
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.core.encryption import decrypt_pii
from app.db import rls
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.schemas.dashboard import DashboardStatsResponse, LoyaltyAnalyticsResponse
from app.services import dashboard_service, messaging_service, storage_service

logger = structlog.get_logger(__name__)

# Doc 5 NICE-04: "Pro: CSV only. Enterprise: CSV + PDF". Matches the
# feature_limits keys seeded in scripts/seed_plans.py.
CSV_EXPORT_FEATURE = "csv_export"
PDF_EXPORT_FEATURE = "pdf_export"

EXPORT_IDEMPOTENCY_TTL_SECONDS = 86400  # generously longer than any Celery retry window


async def generate_and_email_export(
    session: AsyncSession, tenant_id: uuid.UUID, requested_by_user_id: uuid.UUID, export_id: str, export_format: str
) -> None:
    """Build the report, upload it to R2, and email the signed link.

    `export_id` is the Celery idempotency key (AGENTS.md §6: "Always Use
    Idempotency Keys") — minted by the route that queued this task, not here,
    so a retry of the *same* Celery invocation is provably the same logical
    export and can be skipped once it has already gone out.
    """
    idempotency_key = f"export:{export_id}:sent"
    if await cache_service.exists(idempotency_key):
        logger.info("export.already_sent", export_id=export_id)
        return

    # A fresh Celery-task session has no tenant context bound yet — unlike a
    # request handler, there is no get_current_user() dependency to do this.
    # `tenant_id` came from the route that queued this task (the
    # authenticated caller's own tenant_id, not client input), so it's safe
    # to trust for scoping here, same reasoning customer_otp_service applies
    # to a caller-supplied tenant_id with no principal yet.
    await rls.set_tenant_context(session, tenant_id)

    tenant_result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one()

    user_result = await session.execute(select(User).where(User.id == requested_by_user_id))
    user = user_result.scalar_one()

    analytics = await dashboard_service.get_loyalty_analytics(session, branch_id=None)
    stats = await dashboard_service.get_stats(session, can_approve=False)

    if export_format == "pdf":
        body = _build_pdf(tenant.name, analytics, stats)
        content_type = "application/pdf"
    else:
        body = _build_csv(analytics, stats)
        content_type = "text/csv"

    key = storage_service.export_object_key(tenant_id, export_format)
    download_url = storage_service.upload_and_presign(key, body, content_type)

    await messaging_service.send_export_ready_email(
        decrypt_pii(user.encrypted_email),
        tenant_name=tenant.name,
        export_format=export_format.upper(),
        download_url=download_url,
        expires_hours=storage_service.PRESIGNED_URL_TTL_SECONDS // 3600,
    )
    await cache_service.set(idempotency_key, "1", ttl=EXPORT_IDEMPOTENCY_TTL_SECONDS)

    logger.info(
        "export.sent",
        tenant_id=str(tenant_id),
        export_format=export_format,
        export_id=export_id,
    )


def _build_csv(analytics: LoyaltyAnalyticsResponse, stats: DashboardStatsResponse) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(["QuickBite AI + Loyalty — Analytics Export"])
    writer.writerow(["Generated at (UTC)", datetime.now(UTC).isoformat()])
    writer.writerow([])

    writer.writerow(["Summary"])
    writer.writerow(["Average rating", stats.avg_rating])
    writer.writerow(["Review count", stats.review_count])
    writer.writerow(["Total stamps (30 days)", analytics.total_stamps_month])
    writer.writerow(["Active loyalty customers (30 days)", analytics.active_loyalty_customers])
    writer.writerow(["Redemption rate", f"{analytics.redemption_rate:.1%}"])
    writer.writerow([])

    writer.writerow(["Top Customers"])
    writer.writerow(["Phone (masked)", "Total stamps"])
    for customer in analytics.top_customers:
        writer.writerow([customer.phone_masked, customer.total_stamps])
    writer.writerow([])

    writer.writerow(["Branch Comparison (30 days)"])
    writer.writerow(["Branch", "Scan count"])
    for branch in analytics.branch_comparison:
        writer.writerow([branch.branch_name, branch.scan_count])

    return buffer.getvalue().encode("utf-8")


def _build_pdf(
    tenant_name: str, analytics: LoyaltyAnalyticsResponse, stats: DashboardStatsResponse
) -> bytes:
    """QuickBite logo (text wordmark — no binary asset bundled in this repo),
    restaurant name, date range, and summary/branch tables (NICE-04's
    acceptance criteria)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=20 * mm, rightMargin=20 * mm
    )
    styles = getSampleStyleSheet()
    now = datetime.now(UTC)
    story = [
        Paragraph("<b>QuickBite AI + Loyalty</b>", styles["Title"]),
        Paragraph(tenant_name, styles["Heading2"]),
        Paragraph(f"Analytics export — generated {now.strftime('%d %b %Y, %H:%M UTC')}", styles["Normal"]),
        Spacer(1, 10 * mm),
    ]

    summary_rows = [
        ["Metric", "Value"],
        ["Average rating", f"{stats.avg_rating:.1f}" if stats.avg_rating is not None else "—"],
        ["Review count", str(stats.review_count)],
        ["Total stamps (30 days)", str(analytics.total_stamps_month)],
        ["Active loyalty customers (30 days)", str(analytics.active_loyalty_customers)],
        ["Redemption rate", f"{analytics.redemption_rate:.1%}"],
    ]
    story.append(Paragraph("Summary", styles["Heading3"]))
    story.append(_styled_table(summary_rows))
    story.append(Spacer(1, 8 * mm))

    branch_data_rows = [
        [branch.branch_name, str(branch.scan_count)] for branch in analytics.branch_comparison
    ] or [["No branches with activity this period", ""]]
    branch_rows = [["Branch", "Scan count"]] + branch_data_rows
    story.append(Paragraph("Branch Comparison (30 days)", styles["Heading3"]))
    story.append(_styled_table(branch_rows))

    doc.build(story)
    return buffer.getvalue()


def _styled_table(rows: list[list[str]]) -> Table:
    table = Table(rows, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A56DB")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table
