"""QuickBite — Customer: register (encrypted PII), profile, delete.

NEW-OTP-03 implements registration. Profile/delete belong to the customer
profile tickets and are not implemented yet.
"""

import json
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core import cache_service
from app.core.customer_security import create_customer_token
from app.core.encryption import encrypt_pii, sha256_hex
from app.db.models.customer import Customer
from app.schemas.customers import CustomerRegister, CustomerRegisterResponse
from app.services.identity_service import classify_identifier


async def register(request: CustomerRegister, session: AsyncSession) -> tuple[CustomerRegisterResponse, str]:
    pending = await cache_service.get(f"pending_customer_reg:{request.registration_token}")
    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "REGISTRATION_TOKEN_INVALID",
                    "message": "This registration session has expired. Please start again.",
                }
            },
        )
    pending_data = json.loads(pending)
    tenant_id = uuid.UUID(pending_data["tenant_id"])
    identifier = pending_data["identifier"]
    identifier_type = pending_data["identifier_type"]

    if identifier_type == "phone":
        phone = identifier
    else:
        if not request.phone:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": {
                        "code": "PHONE_REQUIRED",
                        "message": "A phone number is required to complete registration.",
                    }
                },
            )
        classify_identifier(request.phone)  # validates E.164 shape, raises 422 if not
        phone = request.phone

    phone_hash = sha256_hex(phone)
    if await _customer_exists(session, tenant_id, Customer.phone_hash, phone_hash):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "CUSTOMER_ALREADY_REGISTERED",
                    "message": "An account with this phone number already exists. Try logging in instead.",
                }
            },
        )

    username_hash = sha256_hex(request.username.lower()) if request.username else None
    if username_hash and await _customer_exists(session, tenant_id, Customer.username_hash, username_hash):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "USERNAME_ALREADY_TAKEN",
                    "message": "That username is already taken.",
                }
            },
        )

    email = request.email or (identifier if identifier_type == "email" else None)
    email_hash = sha256_hex(email.lower()) if email else None

    customer = Customer(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        phone_hash=phone_hash,
        encrypted_phone=encrypt_pii(phone),
        email_hash=email_hash,
        encrypted_email=encrypt_pii(email) if email else None,
        username_hash=username_hash,
        encrypted_username=encrypt_pii(request.username) if request.username else None,
        encrypted_name=encrypt_pii(request.name),
        whatsapp_opt_in=request.whatsapp_opt_in,
    )
    session.add(customer)
    await session.commit()
    await cache_service.delete(f"pending_customer_reg:{request.registration_token}")

    token = create_customer_token(customer.id, customer.tenant_id, customer.phone_hash)
    return CustomerRegisterResponse(customer_id=str(customer.id)), token


async def _customer_exists(
    session: AsyncSession, tenant_id: uuid.UUID, hash_column: InstrumentedAttribute, hash_value: str
) -> bool:
    result = await session.execute(
        select(Customer.id).where(hash_column == hash_value, Customer.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none() is not None
