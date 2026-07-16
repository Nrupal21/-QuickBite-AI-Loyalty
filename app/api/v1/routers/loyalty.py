"""QuickBite — Loyalty routes: /loyalty/scan, /loyalty/card/{id}, /loyalty/menu/{branch_id}."""

from fastapi import APIRouter, Depends, Request
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.customer_auth import get_current_customer_optional
from app.core.rate_limiter import limiter
from app.db.base import get_db
from app.db.models.customer import Customer
from app.schemas.loyalty import ScanRequest, ScanResponse
from app.services.loyalty_service import LoyaltyService

# TODO(LOYALTY-01/02): /loyalty/card/{id}, /loyalty/menu/{branch_id}

router = APIRouter(prefix="/loyalty", tags=["loyalty"])


@router.post("/scan", response_model=ScanResponse, status_code=200)
@limiter.limit("6/hour")
async def scan_loyalty_qr(
    request: Request,
    body: ScanRequest,
    session: AsyncSession = Depends(get_db),
    current_customer: Customer | None = Depends(get_current_customer_optional),
) -> ScanResponse:
    """
    Process a loyalty QR scan. Validates geofence, checks rate limits,
    logs the stamp, and returns updated loyalty status.

    Rate limit: 6 scans per hour per IP.
    """
    service = LoyaltyService(session=session)
    return await service.process_scan(
        qr_token=body.qr_token,
        gps_lat=body.gps_lat,
        gps_lng=body.gps_lng,
        client_ip=get_remote_address(request),
        customer=current_customer,
    )
