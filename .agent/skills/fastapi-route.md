---
name: fastapi-route
description: How to write a FastAPI route for QuickBite — schema first, thin handler, service layer
tags: [fastapi, api, route, pydantic]
---

## When to Use This Skill
- Creating any new API endpoint (REVIEW, LOYALTY, DASH, BILLING tickets)
- Reviewing an existing route for correctness
- "What's the correct pattern for a FastAPI route in this project?"

## The Pattern

```python
# 1. Schema first (app/schemas/loyalty.py)
from pydantic import BaseModel, Field, UUID4
from decimal import Decimal

class ScanRequest(BaseModel):
    qr_token: str = Field(min_length=10, max_length=100)
    gps_lat: float = Field(ge=-90.0, le=90.0)
    gps_lng: float = Field(ge=-180.0, le=180.0)

class ScanResponse(BaseModel):
    stamp_count: int
    reward_progress: float   # 0.0 to 1.0
    reward_unlocked: bool
    redemption_code: str | None = None
    next_reward_at: int      # stamps needed for next reward

# 2. Service layer (app/services/loyalty_service.py)
class LoyaltyService:
    def __init__(self, session: AsyncSession, tenant_id: UUID):
        self.session = session
        self.tenant_id = tenant_id

    async def process_scan(
        self,
        qr_token: str,
        gps_lat: float,
        gps_lng: float,
        customer_id: UUID | None = None,
    ) -> ScanResponse:
        # Business logic here
        ...

# 3. Thin route handler (app/api/v1/loyalty/scan.py)
from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from app.schemas.loyalty import ScanRequest, ScanResponse
from app.services.loyalty_service import LoyaltyService
from app.core.dependencies import get_db, get_tenant_id, get_current_customer_optional

router = APIRouter(prefix="/loyalty", tags=["loyalty"])
limiter = Limiter(key_func=get_remote_address)

@router.post("/scan", response_model=ScanResponse, status_code=200)
@limiter.limit("6/hour")   # 6 scans per hour per IP
async def scan_loyalty_qr(
    request: Request,
    body: ScanRequest,
    session: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_tenant_id),
    current_customer: Customer | None = Depends(get_current_customer_optional),
) -> ScanResponse:
    """
    Process a loyalty QR scan. Validates geofence, checks rate limits,
    logs stamp, and returns updated loyalty status.

    Rate limit: 6 scans per hour per IP.
    """
    service = LoyaltyService(session=session, tenant_id=tenant_id)
    return await service.process_scan(
        qr_token=body.qr_token,
        gps_lat=body.gps_lat,
        gps_lng=body.gps_lng,
        customer_id=current_customer.id if current_customer else None,
    )
```

## Rules for Every Route
- `response_model=` always specified
- `status_code=` always specified (not just default 200)
- Route handler is ≤ 10 lines — all logic in service
- Rate limit decorator on all public endpoints
- Docstring explains what it does and its rate limit
- HTTP errors raised in service, not in route handler

## Error Raising (from service layer)
```python
from fastapi import HTTPException, status

raise HTTPException(
    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    detail={"error": {"code": "SCAN_RATE_LIMIT", "message": "Already stamped today"}},
    headers={"Retry-After": "3600"}
)
```

## Steps
1. Create schema in `app/schemas/{domain}.py`
2. Create service class in `app/services/{domain}_service.py`
3. Create route in `app/api/v1/{domain}/{endpoint_name}.py`
4. Register router in `app/api/v1/__init__.py` (or `app/main.py`)
5. Write test: one test per HTTP status code in Doc 5 acceptance criteria
