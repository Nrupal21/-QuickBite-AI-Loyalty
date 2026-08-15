"""QuickBite — Customer: register (encrypted PII), profile, delete.

NEW-OTP-03 implements registration. Profile/delete belong to the customer
profile tickets and are not implemented yet.
"""

import json
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core import cache_service
from app.core.customer_security import create_customer_token
from app.core.encryption import encrypt_pii, sha256_hex
from app.core.principal import AuthProvider, SubjectType
from app.db.models.customer import Customer
from app.db.models.identity_link import IdentityLink
from app.schemas.customers import CustomerRegister, CustomerRegisterResponse
from app.services import identity_link_service
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
    identifier = pending_data.get("identifier")
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

    # An oauth-originated registration already has a verified email from the
    # provider — trust that over a re-typed one, since the form doesn't ask
    # for it again (see login.html's oauth flow).
    if identifier_type == "oauth":
        email = pending_data.get("verified_email")
    else:
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

    if identifier_type == "oauth":
        await _link_oauth_identity(session, customer, pending_data)

    token = create_customer_token(customer.id, customer.tenant_id, customer.phone_hash)
    return CustomerRegisterResponse(customer_id=str(customer.id)), token


async def _link_oauth_identity(
    session: AsyncSession, customer: Customer, pending_data: dict
) -> None:
    """Attach the identity_links row a Firebase-originated registration still
    needs, so the next "Sign in with Google" click resolves straight to this
    customer instead of starting registration again.

    Best-effort: a failure here must not undo the registration that already
    committed. The customer simply falls back through the registration path
    again next time, which is safe (find_linked_customer would still be a
    miss) if slightly redundant.
    """
    provider = AuthProvider(pending_data["oauth_provider"])
    subject = pending_data["oauth_subject"]
    link = IdentityLink(
        provider=provider.value,
        provider_subject_hash=identity_link_service.subject_hash(provider, subject),
        encrypted_provider_subject=encrypt_pii(subject),
        subject_type=SubjectType.CUSTOMER.value,
        local_id=customer.id,
        tenant_id=customer.tenant_id,
        linked_via=identity_link_service.LINKED_VIA_EMAIL,
    )
    session.add(link)
    try:
        await session.commit()
    except IntegrityError:
        # This Firebase subject was linked (e.g. a concurrent duplicate
        # registration attempt) between the check in customer_oauth_service
        # and here. The customer row this call created is unaffected either
        # way, so just drop the redundant link rather than fail registration.
        await session.rollback()


async def _customer_exists(
    session: AsyncSession, tenant_id: uuid.UUID, hash_column: InstrumentedAttribute, hash_value: str
) -> bool:
    result = await session.execute(
        select(Customer.id).where(hash_column == hash_value, Customer.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none() is not None
