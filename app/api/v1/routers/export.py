"""QuickBite — Analytics export routes: /export/csv (Pro+), /export/pdf
(Enterprise) (NICE-04).

Manager+ — same rank GET /loyalty/analytics and GET /dashboard/stats already
use, since an export is just those same numbers leaving as a file rather
than a screen. Actual report generation happens in a Celery task
(app.workers.tasks.generate_export) — see export_service.py's module
docstring for why.
"""

import uuid

from fastapi import APIRouter, Depends, Request, status

from app.api.v1.dependencies.auth import require_role
from app.api.v1.dependencies.subscription import check_subscription_tier
from app.core.rate_limiter import limiter
from app.core.rbac import RoleLevel
from app.db.models.user import User
from app.schemas.export import ExportQueuedResponse
from app.services.export_service import CSV_EXPORT_FEATURE, PDF_EXPORT_FEATURE
from app.workers.tasks import generate_export

router = APIRouter(prefix="/export", tags=["export"])


def _queue_export(current_user: User, export_format: str) -> ExportQueuedResponse:
    export_id = str(uuid.uuid4())
    generate_export.delay(
        str(current_user.tenant_id), str(current_user.id), export_id, export_format
    )
    return ExportQueuedResponse(status="queued", format=export_format, export_id=export_id)


@router.post("/csv", response_model=ExportQueuedResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/hour")
async def export_csv(
    request: Request,
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    _plan_ok: User = Depends(check_subscription_tier(CSV_EXPORT_FEATURE)),
) -> ExportQueuedResponse:
    return _queue_export(current_user, "csv")


@router.post("/pdf", response_model=ExportQueuedResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/hour")
async def export_pdf(
    request: Request,
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    _plan_ok: User = Depends(check_subscription_tier(PDF_EXPORT_FEATURE)),
) -> ExportQueuedResponse:
    return _queue_export(current_user, "pdf")
