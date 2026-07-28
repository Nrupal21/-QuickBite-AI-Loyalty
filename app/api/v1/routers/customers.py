"""QuickBite — Customer routes: /customers/register, /customers/me, /customers/delete.

NEW-OTP-03 implements /register. /me and /delete belong to the customer
profile tickets and are not implemented yet.
"""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.customer_security import set_customer_session_cookie
from app.core.rate_limiter import limiter
from app.db.base import get_db
from app.schemas.customers import CustomerRegister, CustomerRegisterResponse
from app.services import customer_service

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post("/register", response_model=CustomerRegisterResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register(
    request: Request,
    payload: CustomerRegister,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> CustomerRegisterResponse:
    result, token = await customer_service.register(payload, session)
    set_customer_session_cookie(response, token)
    return result
