"""QuickBite — Customer auth dependencies.

Customers reach the API two ways: the OTP flow's own HS256 cookie/token
(`app/core/customer_security.py`), and — once linked — a Firebase or Supabase
token. `get_current_customer` accepts both by delegating to
`resolve_principal`, then falling back to the OTP token, which the unified
resolver deliberately does not know about (it is a fourth key with a different
claim shape and no `iss`, and folding it into the dispatch table would make
the local branch ambiguous).

`get_current_customer_optional` stays never-raising: LOYALTY-03 allows
anonymous QR scans and only wants a customer_id when a session already exists.
"""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import resolve_principal
from app.core.customer_security import decode_customer_token, read_customer_session_token
from app.core.principal import SubjectType
from app.db import rls
from app.db.base import get_db
from app.db.models.customer import Customer

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "error": {
            "code": "INVALID_TOKEN",
            "message": "Your session has expired. Please sign in again.",
        }
    },
)

_CUSTOMER_REQUIRED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "error": {
            "code": "CUSTOMER_ACCOUNT_REQUIRED",
            "message": "This area is for customer accounts.",
        }
    },
)


async def _customer_from_otp_token(
    request: Request, session: AsyncSession
) -> Customer | None:
    """Resolve the OTP-issued customer token, or None.

    Reads the session cookie as well as the Authorization header. Reading only
    the header made the documented NEW-OTP-02 flow unusable end to end: both
    /auth/customer/otp-verify and /customers/register set an HttpOnly cookie
    and deliberately keep the token out of the JSON body, so a browser had no
    way to produce the header this once demanded — and because the only
    consumer is the *optional* variant, every authenticated scan silently
    degraded to anonymous instead of returning 401.

    Also binds tenant context, which the original LOYALTY-03 implementation
    never did — leaving customer requests with no `app.tenant_id`, so any
    RLS-protected table they touched returned zero rows.
    """
    token = read_customer_session_token(request)
    if token is None:
        return None

    customer_id = decode_customer_token(token)
    if customer_id is None:
        return None

    result = await session.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if customer is not None:
        await rls.set_tenant_context(session, customer.tenant_id)
    return customer


async def get_current_customer_optional(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Customer | None:
    """Return the Customer for a valid bearer token, or None — never raises.

    Anonymous QR scans are allowed; this only links a customer_id when a
    session already exists, whether that session came from OTP or from a
    linked external provider.
    """
    customer = await _customer_from_otp_token(request, session)
    if customer is not None:
        return customer

    try:
        principal = await resolve_principal(request, session)
    except HTTPException:
        # Never raises by contract — an unusable token is simply "anonymous".
        return None
    return principal.customer if principal.subject_type is SubjectType.CUSTOMER else None


async def get_current_customer(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Customer:
    """The authenticated Customer. 401 when absent, 403 for a staff principal."""
    customer = await _customer_from_otp_token(request, session)
    if customer is not None:
        return customer

    principal = await resolve_principal(request, session)
    if principal.subject_type is not SubjectType.CUSTOMER or principal.customer is None:
        raise _CUSTOMER_REQUIRED
    return principal.customer


def require_customer_session():
    """Dependency factory for routes that must have a real customer session.

    A factory rather than a plain dependency so it reads the same as
    `require_role(...)` at the call site and has somewhere to grow (blocked
    checks, per-branch scoping) without changing every route signature.
    """

    async def _dependency(
        customer: Customer = Depends(get_current_customer),
    ) -> Customer:
        if customer.is_blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "CUSTOMER_BLOCKED",
                        "message": "This account has been blocked.",
                    }
                },
            )
        return customer

    return _dependency
