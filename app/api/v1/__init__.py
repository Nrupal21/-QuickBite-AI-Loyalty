"""QuickBite AI + Loyalty — API v1 Package."""

from fastapi import APIRouter

from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.loyalty import router as loyalty_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(loyalty_router)
