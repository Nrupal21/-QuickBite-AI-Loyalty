"""IdentityLink — maps an external auth subject onto a local principal.

The cross-reference that lets a Supabase or Firebase token authenticate as an
existing `restaurant.users` or `customer.customers` row. Deliberately an
explicit table rather than matching on the token's email claim at request
time: an external provider's email is that provider's *assertion* about who
someone is, and using it as a join key means anyone who can register that
address at the provider inherits the local account.

Not RLS-protected — see the comment in migration 0005. It is the bootstrap
table; the tenant is unknown until it is read.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IdentityLink(Base):
    """One row per (external identity, local principal) pair."""

    __tablename__ = "identity_links"
    __table_args__ = (
        # One external subject authenticates as exactly one local principal.
        UniqueConstraint(
            "provider", "provider_subject_hash", name="uq_identity_links_provider_subject"
        ),
        # And one local principal has at most one identity per provider.
        UniqueConstraint(
            "provider", "subject_type", "local_id", name="uq_identity_links_provider_local"
        ),
        {"schema": "restaurant"},
    )

    provider: Mapped[str] = mapped_column(String)  # supabase | firebase
    # sha256("<provider>:<sub>") — the provider prefix is inside the hash so a
    # Firebase subject can never collide with a Supabase one.
    provider_subject_hash: Mapped[str] = mapped_column(String)
    encrypted_provider_subject: Mapped[str] = mapped_column(String)  # AES-256-GCM
    subject_type: Mapped[str] = mapped_column(String)  # user | customer
    # No ForeignKey: this points at restaurant.users OR customer.customers
    # depending on subject_type, and PostgreSQL has no polymorphic FK.
    local_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # Nullable for a standard user (role USER) who signed in with OAuth before
    # registering a restaurant — same reasoning as User.tenant_id. A Customer
    # link always carries a concrete tenant.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurant.tenants.id"), nullable=True, index=True
    )
    linked_via: Mapped[str] = mapped_column(String)  # explicit | email | phone
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Unlinking sets this False rather than deleting, so the audit trail keeps
    # showing that the identity was once attached.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
