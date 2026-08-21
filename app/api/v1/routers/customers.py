"""QuickBite — Customer routes: /customers/register, /customers/me, /customers/delete.

NEW-OTP-03 implements /register. /me is the customer-profile ticket ("My
Rewards" — stamps by location, review-draft history). /delete is still not
implemented.
"""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.customer_auth import require_customer_session
from app.core.customer_security import set_customer_session_cookie
from app.core.rate_limiter import limiter
from app.db.base import get_db
from app.db.models.customer import Customer
from app.schemas.customers import CustomerProfileResponse, CustomerRegister, CustomerRegisterResponse
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


@router.get("/me", response_model=CustomerProfileResponse, status_code=status.HTTP_200_OK)
async def read_profile(
    current_customer: Customer = Depends(require_customer_session()),
    session: AsyncSession = Depends(get_db),
) -> CustomerProfileResponse:
    """"My Rewards": stamps collected at every branch of this restaurant, and
    the diner's own history of AI-drafted reviews sent while signed in."""
    return await customer_service.get_profile(session, current_customer)
