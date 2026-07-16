"""QuickBite — Owner/Staff auth routes: /auth/register, /auth/login, /auth/mfa/*.

AUTH-01: registration + email verification. Login/MFA/refresh routes arrive
with AUTH-02/AUTH-03.
"""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import limiter
from app.db.base import get_db
from app.schemas.auth import RegisterResponse, UserRegister, VerifyEmailResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    payload: UserRegister,
    session: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    verification_base_url = str(request.base_url) + "api/v1/auth/verify-email"
    return await AuthService(session=session).register(payload, verification_base_url)


@router.get("/verify-email", response_model=VerifyEmailResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    token: str,
    session: AsyncSession = Depends(get_db),
) -> VerifyEmailResponse:
    return await AuthService(session=session).verify_email(token)
