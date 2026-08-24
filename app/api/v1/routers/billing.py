"""QuickBite — Billing routes: /billing/* (owner-authenticated) and
/webhooks/razorpay (unauthenticated, HMAC-signed).

The webhook route intentionally does not depend on `get_current_user` — it
has no bearer token, and its own HMAC signature is the authentication. The
raw request body is read and verified BEFORE anything parses it (SEC-10
equivalent): Razorpay computes the signature over the exact bytes it sent, so
signature verification must happen first or not at all.
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import get_current_user, require_role
from app.core import razorpay_signature
from app.core.config import settings
from app.core.rate_limiter import limiter
from app.core.rbac import RoleLevel
from app.db.base import get_db
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.schemas.billing import (
    CheckoutRequest,
    CheckoutResponse,
    PlanOut,
    SubscriptionStatusResponse,
    WebhookAckResponse,
)
from app.services.billing_service import BillingService

router = APIRouter(prefix="/billing", tags=["billing"])
webhook_router = APIRouter(tags=["billing"])


@router.get("/plans", response_model=list[PlanOut])
@limiter.limit("30/minute")
async def list_plans(
    request: Request,
    category_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[PlanOut]:
    """Public — the registration/onboarding screens need plan names, prices,
    and ids before the caller has any session (register-restaurant needs a
    real plan_id).

    `category_id` is the business category "Join Us" just collected: pricing
    is shown per category, so a food truck is never offered the unlimited-
    branch Enterprise plan. Omitting it returns every active plan, which is
    what the public pricing page on the landing site wants.
    """
    return await BillingService(session=session).list_active_plans(category_id)


@router.get("/subscription", response_model=SubscriptionStatusResponse)
async def get_subscription(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> SubscriptionStatusResponse:
    return await BillingService(session=session).get_subscription_status(current_user.tenant_id)


@router.post(
    "/checkout", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("10/hour")
async def create_checkout(
    request: Request,
    payload: CheckoutRequest,
    # Doc 3: only an Owner (or Super Admin, which require_role admits at any
    # lower min_level per its "at least this senior" contract) may change a
    # tenant's subscription. Previously plain get_current_user — any Manager
    # or Staff account could call this directly, regardless of what the
    # dashboard UI chose to render.
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    tenant_result = await session.execute(
        select(Tenant).where(Tenant.id == current_user.tenant_id)
    )
    tenant = tenant_result.scalar_one()
    return await BillingService(session=session).create_checkout_order(tenant, payload.plan_id)


@router.post("/cancel", response_model=SubscriptionStatusResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/hour")
async def cancel_subscription(
    request: Request,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> SubscriptionStatusResponse:
    return await BillingService(session=session).cancel_subscription(
        current_user.tenant_id, current_user
    )


@webhook_router.post(
    "/webhooks/razorpay", response_model=WebhookAckResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("300/minute")
async def razorpay_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> WebhookAckResponse:
    # Raw bytes, read before any parsing — the signature covers exactly what
    # Razorpay sent, and a decode/re-encode round trip changes the digest.
    raw_body = await request.body()
    signature = request.headers.get(razorpay_signature.SIGNATURE_HEADER)
    razorpay_signature.verify_webhook_signature(
        raw_body, signature, settings.RAZORPAY_WEBHOOK_SECRET
    )

    result = await BillingService(session=session).handle_webhook(raw_body, request.headers)
    return WebhookAckResponse(**result)
