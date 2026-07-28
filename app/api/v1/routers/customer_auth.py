"""QuickBite — Customer OTP routes: /auth/customer/otp-request, otp-verify.

NEW-OTP-01/02. The Customer JWT is set as an HttpOnly cookie, never
returned in the JSON body (Doc 3).
"""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.customer_security import set_customer_session_cookie
from app.core.rate_limiter import limiter
from app.db.base import get_db
from app.schemas.customer_auth import (
    OTPNewUserResponse,
    OTPRequest,
    OTPSentResponse,
    OTPVerifiedResponse,
    OTPVerify,
)
from app.services import customer_otp_service

router = APIRouter(prefix="/auth/customer", tags=["customer-auth"])


@router.post("/otp-request", status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
async def otp_request(
    request: Request,
    payload: OTPRequest,
    session: AsyncSession = Depends(get_db),
) -> OTPSentResponse | OTPNewUserResponse:
    return await customer_otp_service.request_otp(payload, session)


@router.post("/otp-verify", response_model=OTPVerifiedResponse, status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
async def otp_verify(
    request: Request,
    payload: OTPVerify,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> OTPVerifiedResponse:
    result, token = await customer_otp_service.verify_otp(payload, session)
    set_customer_session_cookie(response, token)
    return result
