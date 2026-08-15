"""Maps a verified external auth subject onto a local principal.

The token has already been verified by the time anything here runs — this
module answers the *next* question: which QuickBite account is this, and may
it exist at all?

Two rules drive the whole design.

**Staff accounts are never created just-in-time.** Anyone can self-register in
a Supabase or Firebase project. If first contact minted a `restaurant.users`
row we would have open registration *with a role attached*, and the tenant
would be a guess. Staff identities are attached only through
`POST /auth/link/{provider}`, which requires a valid local session as proof of
who you already are.

**Email is never a join key unless the provider says it is verified.** A
Firebase email/password signup starts with `email_verified=false`; treating
that address as an identity lets an attacker register a victim's email at the
provider and inherit their local account — for a customer, their stamps and
reward balance.
"""

import uuid
from typing import Any

import structlog
from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.encryption import encrypt_pii, sha256_hex
from app.core.principal import AuthProvider, Principal, SubjectType
from app.db import bootstrap, rls
from app.db.models.audit import AuditLog
from app.db.models.customer import Customer
from app.db.models.identity_link import IdentityLink
from app.db.models.user import User

logger = structlog.get_logger(__name__)

LINKED_VIA_EXPLICIT = "explicit"
LINKED_VIA_EMAIL = "email"
LINKED_VIA_PHONE = "phone"

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "error": {
            "code": "INVALID_TOKEN",
            "message": "Your session has expired. Please log in again.",
        }
    },
)

_LINK_REQUIRED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "error": {
            "code": "IDENTITY_LINK_REQUIRED",
            "message": "Link this sign-in method from your account settings first.",
        }
    },
)

_TENANT_UNRESOLVED = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail={
        "error": {
            "code": "TENANT_UNRESOLVED",
            "message": "This restaurant could not be identified from the request.",
        }
    },
)

_ALREADY_LINKED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail={
        "error": {
            "code": "IDENTITY_ALREADY_LINKED",
            "message": "That sign-in method is already linked to an account.",
        }
    },
)

_STAFF_LINKED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "error": {
            "code": "STAFF_ACCOUNT_LINKED",
            "message": "This sign-in method belongs to a staff account. Use the password login instead.",
        }
    },
)

_CUSTOMER_BLOCKED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "error": {
            "code": "CUSTOMER_BLOCKED",
            "message": "This account has been blocked.",
        }
    },
)


def subject_hash(provider: AuthProvider, subject: str) -> str:
    """SHA-256 of "<provider>:<sub>".

    The provider must be inside the hash. Firebase and Supabase both mint
    opaque subject strings from their own namespaces, and without the prefix a
    collision — accidental or engineered — would authenticate one provider's
    user as another's.
    """
    return sha256_hex(f"{provider.value}:{subject}")


def normalise_email(email: str) -> str:
    """Lowercase and strip, matching how registration hashes an address.

    Any divergence between this and the normalisation used at registration
    means the hashes never match and linking silently fails forever — a bug
    that presents as "the JWT is wrong" and wastes hours.
    """
    return email.strip().lower()


# --- Resolution ---------------------------------------------------------


async def resolve(
    request: Request,
    session: AsyncSession,
    provider: AuthProvider,
    subject: str,
    claims: dict[str, Any],
) -> Principal:
    """Find (or, for customers only, provision) the local principal for a
    verified external token."""
    link = await _get_link(session, provider, subject)

    if link is not None:
        if not link.is_active:
            raise _UNAUTHORIZED
        # Bind the tenant BEFORE loading the subject row. Two benefits: the
        # subject SELECT is then itself RLS-checked, so a wrong tenant_id here
        # returns zero rows and 401s rather than leaking across tenants.
        await rls.set_tenant_context(session, link.tenant_id)
        return await _principal_from_link(session, link, provider, subject, claims)

    if not settings.EXTERNAL_AUTH_JIT_ENABLED:
        logger.info(
            "auth.identity.link_missing", provider=provider.value, jit_enabled=False
        )
        raise _LINK_REQUIRED

    return await _provision_customer(request, session, provider, subject, claims)


async def _get_link(
    session: AsyncSession, provider: AuthProvider, subject: str
) -> IdentityLink | None:
    """Resolve the external subject to a tenant, bind it, then read the row.

    `restaurant.identity_links` is RLS-protected (migration 0008), and this
    lookup is the fifth bootstrap case: a Supabase or Firebase token names a
    subject in the provider's namespace, so working out which tenant it belongs
    to is the whole point — there is nothing to scope by beforehand. Querying
    directly would return no rows and turn every external login into a silent
    "unknown identity".
    """
    provider_subject_hash = subject_hash(provider, subject)

    tenant_id = await bootstrap.tenant_for_identity_link(
        session, provider.value, provider_subject_hash
    )
    if tenant_id is None:
        return None

    await rls.set_tenant_context(session, tenant_id)
    result = await session.execute(
        select(IdentityLink).where(
            IdentityLink.provider == provider.value,
            IdentityLink.provider_subject_hash == provider_subject_hash,
        )
    )
    return result.scalar_one_or_none()


async def _principal_from_link(
    session: AsyncSession,
    link: IdentityLink,
    provider: AuthProvider,
    subject: str,
    claims: dict[str, Any],
) -> Principal:
    if link.subject_type == SubjectType.USER.value:
        result = await session.execute(select(User).where(User.id == link.local_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise _UNAUTHORIZED
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "ACCOUNT_DEACTIVATED",
                        "message": "This account has been deactivated. "
                        "Contact your restaurant owner.",
                    }
                },
            )
        return Principal(
            subject_type=SubjectType.USER,
            tenant_id=user.tenant_id,
            auth_provider=provider,
            provider_subject=subject,
            claims=claims,
            user=user,
        )

    result = await session.execute(select(Customer).where(Customer.id == link.local_id))
    customer = result.scalar_one_or_none()
    if customer is None:
        raise _UNAUTHORIZED
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
    return Principal(
        subject_type=SubjectType.CUSTOMER,
        tenant_id=customer.tenant_id,
        auth_provider=provider,
        provider_subject=subject,
        claims=claims,
        customer=customer,
    )


# --- Just-in-time customer provisioning ---------------------------------


def _resolve_tenant_id(request: Request) -> uuid.UUID:
    """Tenant for a JIT customer, from the request host only.

    Never from the token: `user_metadata` is writable by the end user and even
    `app_metadata` is an external system asserting *our* tenant boundary. Never
    from a header either — that is attacker-chosen. The subdomain is bound to
    DNS we control, which is what makes it trustworthy.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise _TENANT_UNRESOLVED
    return tenant_id


def _verified_email(claims: dict[str, Any]) -> str | None:
    """The email claim, but only when the provider vouches for it.

    Firebase puts `email_verified` at the top level. Supabase mirrors it into
    `user_metadata` and separately sets `email_confirmed_at`; requiring both
    avoids trusting a metadata field the user may be able to influence.
    """
    email = claims.get("email")
    if not email:
        return None
    if claims.get("email_verified") is True:
        return normalise_email(email)
    metadata = claims.get("user_metadata") or {}
    if metadata.get("email_verified") is True and claims.get("email_confirmed_at"):
        return normalise_email(email)
    return None


def _verified_phone(claims: dict[str, Any]) -> str | None:
    """The phone claim when the provider verified it.

    Firebase phone sign-in produces a verified `phone_number` by construction —
    the OTP *is* the verification — which makes phone the strongest join key
    available, and the natural one for a phone-first product.
    """
    phone = claims.get("phone_number") or claims.get("phone")
    if not phone:
        return None
    if claims.get("phone_verified") is False:
        return None
    return str(phone).strip()


async def _provision_customer(
    request: Request,
    session: AsyncSession,
    provider: AuthProvider,
    subject: str,
    claims: dict[str, Any],
) -> Principal:
    """Attach to (or create) a Customer for a first-time external sign-in.

    Only customers — `customer.customers` is a low-privilege, tenant-scoped
    record with no role attached. A staff User is never created here.
    """
    tenant_id = _resolve_tenant_id(request)
    await rls.set_tenant_context(session, tenant_id)

    phone = _verified_phone(claims)
    email = _verified_email(claims)

    customer = await _find_customer(session, tenant_id, phone, email)
    linked_via = LINKED_VIA_PHONE if phone else LINKED_VIA_EMAIL

    if customer is None:
        customer = Customer(
            tenant_id=tenant_id,
            phone_hash=sha256_hex(phone) if phone else None,
            encrypted_phone=encrypt_pii(phone) if phone else None,
            # An UNVERIFIED email is stored nowhere searchable. Writing it to
            # email_hash would let the next holder of that address link to this
            # customer, which is the takeover this whole module exists to stop.
            email_hash=sha256_hex(email) if email else None,
            encrypted_email=encrypt_pii(email) if email else None,
        )
        session.add(customer)
        await session.flush()
        linked_via = LINKED_VIA_PHONE if phone else LINKED_VIA_EMAIL

    link = IdentityLink(
        provider=provider.value,
        provider_subject_hash=subject_hash(provider, subject),
        encrypted_provider_subject=encrypt_pii(subject),
        subject_type=SubjectType.CUSTOMER.value,
        local_id=customer.id,
        tenant_id=tenant_id,
        linked_via=linked_via,
    )
    session.add(link)
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            action="identity_link_provisioned",
            resource_type="identity_link",
            resource_id=customer.id,
            # Type and provider only — never the subject or the email itself.
            event_metadata={"provider": provider.value, "linked_via": linked_via},
        )
    )

    try:
        await session.commit()
    except IntegrityError:
        # Two concurrent first requests both saw no link. The unique constraint
        # is the arbiter — never a check-then-insert, which has a window.
        await session.rollback()
        await rls.set_tenant_context(session, tenant_id)
        existing = await _get_link(session, provider, subject)
        if existing is None:
            raise
        return await _principal_from_link(session, existing, provider, subject, claims)

    logger.info(
        "auth.identity.provisioned",
        provider=provider.value,
        tenant_id=str(tenant_id),
        customer_id=str(customer.id),
        linked_via=linked_via,
    )
    return Principal(
        subject_type=SubjectType.CUSTOMER,
        tenant_id=tenant_id,
        auth_provider=provider,
        provider_subject=subject,
        claims=claims,
        customer=customer,
    )


async def _find_customer(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    phone: str | None,
    email: str | None,
) -> Customer | None:
    """Existing customer matching a provider-verified phone, else email.

    Phone first: it is verified by construction on a Firebase phone sign-in,
    and `customers.phone_hash` is the identifier the OTP flow already uses.
    """
    if phone:
        result = await session.execute(
            select(Customer).where(
                Customer.tenant_id == tenant_id,
                Customer.phone_hash == sha256_hex(phone),
            )
        )
        found = result.scalar_one_or_none()
        if found is not None:
            return found
    if email:
        result = await session.execute(
            select(Customer).where(
                Customer.tenant_id == tenant_id,
                Customer.email_hash == sha256_hex(email),
            )
        )
        return result.scalar_one_or_none()
    return None


# --- Existing-link lookup (no provisioning) -----------------------------


async def find_linked_customer(
    session: AsyncSession, provider: AuthProvider, subject: str
) -> Customer | None:
    """The Customer already linked to this verified external subject, or None.

    Deliberately does not provision on a miss — unlike `resolve()`'s JIT path,
    a fresh Customer here would need a phone number, which no social sign-in
    ever supplies. A caller that gets None routes the person through the
    ordinary registration flow (`customer_service.register()`) instead, which
    is the only place that collects one.

    Raises 403 rather than returning a Customer if the subject is linked to a
    *staff* User — the caller is a public customer-sign-in surface, and
    silently treating that as "no customer" would let it fall through to
    starting a brand-new, unrelated customer registration for someone who
    already has a staff account.
    """
    link = await _get_link(session, provider, subject)
    if link is None:
        return None
    if not link.is_active:
        raise _UNAUTHORIZED
    await rls.set_tenant_context(session, link.tenant_id)
    if link.subject_type != SubjectType.CUSTOMER.value:
        raise _STAFF_LINKED

    result = await session.execute(select(Customer).where(Customer.id == link.local_id))
    customer = result.scalar_one_or_none()
    if customer is None:
        raise _UNAUTHORIZED
    if customer.is_blocked:
        raise _CUSTOMER_BLOCKED
    return customer


# --- Explicit linking ---------------------------------------------------


async def link_to_user(
    session: AsyncSession,
    user: User,
    provider: AuthProvider,
    subject: str,
) -> IdentityLink:
    """Attach a verified external identity to an already-authenticated staff user.

    The caller proves two things independently: a valid local session (who they
    are) and a valid external token (that they control that provider account).
    Neither alone is sufficient, and no email is trusted at any point.
    """
    link = IdentityLink(
        provider=provider.value,
        provider_subject_hash=subject_hash(provider, subject),
        encrypted_provider_subject=encrypt_pii(subject),
        subject_type=SubjectType.USER.value,
        local_id=user.id,
        tenant_id=user.tenant_id,
        linked_via=LINKED_VIA_EXPLICIT,
    )
    session.add(link)
    session.add(
        AuditLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action="identity_link_created",
            resource_type="identity_link",
            event_metadata={"provider": provider.value, "linked_via": LINKED_VIA_EXPLICIT},
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        # Either this external subject is already linked elsewhere, or this
        # user already has an identity with this provider. Both are 409s, and
        # both are enforced by uq_identity_links_* rather than a prior SELECT.
        await session.rollback()
        raise _ALREADY_LINKED from exc

    logger.info(
        "auth.identity.linked",
        provider=provider.value,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
    )
    return link
