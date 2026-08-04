"""QuickBite — Unified identify-first login lookup.

Checks whether an identifier (email, phone, or username) belongs to an
Owner/Staff account (`restaurant.users`, tenant-scoped) or a Customer
account (`customer.customers`, tenant-scoped), so the login screen can
render the right method (password vs OTP) or offer registration. If both
match, the Owner/Staff account takes precedence. Unlike the OTP-request
enumeration guard, this endpoint deliberately reveals whether an identifier
is registered — that's the point of the feature — so the mitigation is
rate limiting (at the route) and an audit trail, not hiding the result.

Username is login-only (`/auth/identify`) — there's no way to deliver an
OTP to a username, so `classify_identifier()` (used by the OTP request/
verify and phone-field validation elsewhere) deliberately stays email/phone
only; widening it to accept username shapes would let arbitrary strings
pass as a "valid phone number" there. `classify_login_identifier()` is the
three-way version used only here.
"""

import re

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import sha256_hex
from app.db.models.audit import AuditLog
from app.db.models.customer import Customer
from app.db.models.user import User
from app.schemas.auth import IdentifyRequest, IdentifyResponse

logger = structlog.get_logger(__name__)

_PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")  # E.164
_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.]{3,30}$")


def classify_identifier(identifier: str) -> str:
    """Return "email" or "phone", or raise 422 if neither shape matches."""
    if "@" in identifier:
        return "email"
    if _PHONE_PATTERN.fullmatch(identifier):
        return "phone"
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": {
                "code": "IDENTIFIER_INVALID",
                "message": "Enter a valid email address or a phone number in +<country><number> format.",
            }
        },
    )


def classify_login_identifier(identifier: str) -> str:
    """Return "email", "phone", or "username" for /auth/identify; 422 if none match."""
    if "@" in identifier:
        return "email"
    if _PHONE_PATTERN.fullmatch(identifier):
        return "phone"
    if _USERNAME_PATTERN.fullmatch(identifier):
        return "username"
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": {
                "code": "IDENTIFIER_INVALID",
                "message": (
                    "Enter a valid email, a phone number in +<country><number> format, "
                    "or a username (3-30 letters, numbers, dots, or underscores)."
                ),
            }
        },
    )


def classify_staff_identifier(identifier: str) -> str:
    """Return "email" or "username" for /auth/login; 422 if neither shape matches.

    Narrower than classify_login_identifier() by one case: phone. `/auth/identify`
    never resolves a staff account by phone (see _STAFF_HASH_COLUMN below), so
    accepting one at login could only ever fail — and `users.phone_hash` is
    non-unique anyway, which makes a phone an ambiguous credential with no
    tenant context on this endpoint to disambiguate it.
    """
    if "@" in identifier:
        return "email"
    if _USERNAME_PATTERN.fullmatch(identifier):
        return "username"
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": {
                "code": "IDENTIFIER_INVALID",
                "message": (
                    "Enter the email address or username for your account "
                    "(3-30 letters, numbers, dots, or underscores)."
                ),
            }
        },
    )


# Staff lookups are email/username only — both are globally unique columns.
# `users.phone_hash` is indexed but NOT unique, so it cannot identify a single
# account; staff phone stays a contact/recovery field.
_STAFF_HASH_COLUMN = {"email": User.email_hash, "username": User.username_hash}
_CUSTOMER_HASH_COLUMN = {
    "email": Customer.email_hash,
    "phone": Customer.phone_hash,
    "username": Customer.username_hash,
}


async def identify(request: IdentifyRequest, session: AsyncSession) -> IdentifyResponse:
    identifier_type = classify_login_identifier(request.identifier)
    lookup_value = request.identifier.lower() if identifier_type in ("email", "username") else request.identifier
    identifier_hash = sha256_hex(lookup_value)

    staff_user_id = None
    staff_hash_column = _STAFF_HASH_COLUMN.get(identifier_type)
    if staff_hash_column is not None:
        result = await session.execute(
            select(User.id).where(
                staff_hash_column == identifier_hash, User.tenant_id == request.tenant_id
            )
        )
        staff_user_id = result.scalar_one_or_none()

    customer_id = None
    if staff_user_id is None:
        customer_hash_column = _CUSTOMER_HASH_COLUMN[identifier_type]
        result = await session.execute(
            select(Customer.id).where(
                customer_hash_column == identifier_hash, Customer.tenant_id == request.tenant_id
            )
        )
        customer_id = result.scalar_one_or_none()

    if staff_user_id is not None:
        response = IdentifyResponse(found=True, account_type="staff", available_methods=["password"])
    elif customer_id is not None:
        response = IdentifyResponse(found=True, account_type="customer", available_methods=["otp"])
    else:
        response = IdentifyResponse(found=False, account_type=None, available_methods=[])

    session.add(
        AuditLog(
            tenant_id=request.tenant_id,
            action="identify_lookup",
            resource_type="identity",
            event_metadata={"identifier_type": identifier_type, "found": response.found},
        )
    )
    await session.commit()

    logger.info(
        "auth.identify.lookup",
        tenant_id=str(request.tenant_id),
        identifier_type=identifier_type,
        found=response.found,
    )
    return response
