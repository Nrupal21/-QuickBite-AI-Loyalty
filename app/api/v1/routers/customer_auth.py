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
    OAuthNewUserResponse,
    OAuthSignInRequest,
    OTPNewUserResponse,
    OTPRequest,
    OTPSentResponse,
    OTPVerifiedResponse,
    OTPVerify,
)
from app.services import customer_oauth_service, customer_otp_service

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


@router.post("/oauth", status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
async def oauth_sign_in(
    request: Request,
    payload: OAuthSignInRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> OTPVerifiedResponse | OAuthNewUserResponse:
    """Sign in with Google, Apple, Microsoft, GitHub, or Twitter/X.

    Firebase's client SDK abstracts all five into one ID token, so there is
    exactly one backend endpoint regardless of which button the customer
    clicked (see customer_oauth_service for the two-branch logic).
    """
    result, token = await customer_oauth_service.sign_in(payload, session)
    if token is not None:
        set_customer_session_cookie(response, token)
    return result
