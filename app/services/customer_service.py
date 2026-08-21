"""QuickBite — Customer: register (encrypted PII), profile, delete.

NEW-OTP-03 implements registration. Profile/delete belong to the customer
profile tickets and are not implemented yet.
"""

import json
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core import cache_service
from app.core.customer_security import create_customer_token
from app.core.encryption import decrypt_pii, encrypt_pii, sha256_hex
from app.core.principal import AuthProvider, SubjectType
from app.db import rls
from app.db.models.branch import Branch
from app.db.models.customer import Customer, ReviewDraft
from app.db.models.identity_link import IdentityLink
from app.db.models.loyalty import StampLog
from app.schemas.customers import (
    CustomerProfileResponse,
    CustomerRegister,
    CustomerRegisterResponse,
    LocationStampsOut,
    ReviewDraftOut,
)
from app.services import identity_link_service
from app.services.identity_service import classify_identifier

# Recent drafts only — this is a profile-page preview, not a full export.
_RECENT_REVIEW_DRAFTS_LIMIT = 20


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

    # customer.customers is RLS-protected and nothing has authenticated yet on
    # this request — the tenant_id came from the registration_token's own
    # pending-registration record (itself only mintable via a prior
    # /otp-request for this tenant), so it's safe to bind before the lookup
    # and insert below run.
    await rls.set_tenant_context(session, tenant_id)

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


async def get_profile(session: AsyncSession, customer: Customer) -> CustomerProfileResponse:
    """GET /customers/me — "My Rewards": stamps grouped by branch (location),
    plus the customer's own recent review-draft history.

    Both queries are scoped by `customer_id = :cid` on top of the RLS
    tenant_id filter already bound by `get_current_customer` — RLS alone
    would return every diner's rows for this tenant, not just this one's.
    """
    stamps_result = await session.execute(
        select(
            Branch.id,
            Branch.name,
            Branch.encrypted_address,
            func.count(StampLog.id),
            func.max(StampLog.scanned_at),
        )
        .join(Branch, Branch.id == StampLog.branch_id)
        .where(StampLog.customer_id == customer.id, StampLog.is_fraudulent.is_(False))
        .group_by(Branch.id, Branch.name, Branch.encrypted_address)
        .order_by(func.max(StampLog.scanned_at).desc())
    )
    stamps_by_location = [
        LocationStampsOut(
            branch_id=str(branch_id),
            branch_name=branch_name,
            branch_address=decrypt_pii(encrypted_address) if encrypted_address else None,
            stamp_count=stamp_count,
            last_scanned_at=last_scanned_at,
        )
        for branch_id, branch_name, encrypted_address, stamp_count, last_scanned_at in stamps_result.all()
    ]

    drafts_result = await session.execute(
        select(ReviewDraft, Branch.name)
        .join(Branch, Branch.id == ReviewDraft.branch_id)
        .where(ReviewDraft.customer_id == customer.id)
        .order_by(ReviewDraft.created_at.desc())
        .limit(_RECENT_REVIEW_DRAFTS_LIMIT)
    )
    recent_review_drafts = [
        ReviewDraftOut(
            id=str(draft.id),
            branch_name=branch_name,
            rating=draft.rating,
            tags=draft.tags,
            draft_excerpt=draft.draft_excerpt,
            created_at=draft.created_at,
        )
        for draft, branch_name in drafts_result.all()
    ]

    return CustomerProfileResponse(
        customer_id=str(customer.id),
        name=decrypt_pii(customer.encrypted_name) if customer.encrypted_name else None,
        email=decrypt_pii(customer.encrypted_email) if customer.encrypted_email else None,
        username=decrypt_pii(customer.encrypted_username) if customer.encrypted_username else None,
        member_since=customer.created_at,
        total_stamps_alltime=customer.total_stamps_alltime,
        current_reward_count=customer.current_reward_count,
        stamps_by_location=stamps_by_location,
        recent_review_drafts=recent_review_drafts,
    )


async def _customer_exists(
    session: AsyncSession, tenant_id: uuid.UUID, hash_column: InstrumentedAttribute, hash_value: str
) -> bool:
    result = await session.execute(
        select(Customer.id).where(hash_column == hash_value, Customer.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none() is not None
