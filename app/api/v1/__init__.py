"""QuickBite AI + Loyalty — API v1 Package."""

from fastapi import APIRouter

from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.billing import router as billing_router
from app.api.v1.routers.billing import webhook_router as billing_webhook_router
from app.api.v1.routers.customer_auth import router as customer_auth_router
from app.api.v1.routers.customers import router as customers_router
from app.api.v1.routers.loyalty import router as loyalty_router
from app.api.v1.routers.reputation import router as reputation_router
from app.api.v1.routers.team import router as team_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(billing_router)
# Registered separately from billing_router (which carries prefix="/billing")
# so the URL is /api/v1/webhooks/razorpay, not /api/v1/billing/webhooks/razorpay
# — a webhook URL configured in the Razorpay dashboard should not have to move
# if /billing/* routes are ever reorganised.
api_router.include_router(billing_webhook_router)
api_router.include_router(customer_auth_router)
api_router.include_router(customers_router)
api_router.include_router(loyalty_router)
api_router.include_router(reputation_router)
api_router.include_router(team_router)
