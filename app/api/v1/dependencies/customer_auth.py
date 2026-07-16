"""QuickBite — Customer auth dependencies: get_current_customer, require_customer_session().

get_current_customer (required, raises 401) and require_customer_session()
belong to the customer dashboard/profile tickets and are not implemented
yet. Only the optional variant LOYALTY-03 needs is built here.
"""

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.customer_security import decode_customer_token
from app.db.base import get_db
from app.db.models.customer import Customer


async def get_current_customer_optional(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Customer | None:
    """Return the Customer for a valid bearer token, or None — never raises.

    Anonymous QR scans are allowed; this only links a customer_id when an
    OTP session already exists.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None

    customer_id = decode_customer_token(auth_header.removeprefix("Bearer "))
    if customer_id is None:
        return None

    result = await session.execute(select(Customer).where(Customer.id == customer_id))
    return result.scalar_one_or_none()
