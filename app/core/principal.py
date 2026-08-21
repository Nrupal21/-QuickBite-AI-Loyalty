"""Principal — the one authenticated identity every verifier resolves to.

Three token families reach this API: the local HS256 token minted by
AuthService, Supabase Auth JWTs (ES256/RS256 via JWKS), and Firebase ID tokens
(RS256). Rather than teaching every route about three shapes, each verifier
resolves to this single object and downstream code stays provider-blind.

The invariant that makes the whole design work: `user` and `customer` hold
real ORM rows loaded from *our* database, and `tenant_id`/authorisation always
derive from those rows — never from a token claim. That is why `require_role`
needs no knowledge of providers at all: it receives an ordinary `User` and
reads the role from Postgres exactly as it always has.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a model import cycle
    from app.db.models.customer import Customer
    from app.db.models.user import User


class AuthProvider(str, Enum):
    """Which verifier authenticated this request."""

    LOCAL = "local"
    SUPABASE = "supabase"
    FIREBASE = "firebase"


class SubjectType(str, Enum):
    """Staff/owner (`restaurant.users`) vs. diner (`customer.customers`).

    Mirrors `identity_links.subject_type`. The two live in different schemas
    with different privilege models, so conflating them is a privilege bug
    waiting to happen — `get_current_user` refuses a CUSTOMER outright.
    """

    USER = "user"
    CUSTOMER = "customer"


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller. Frozen: nothing downstream may re-point it at
    another tenant or subject after the resolver has vouched for it."""

    subject_type: SubjectType
    # None for a standard user (role USER) with no restaurant registered yet.
    # Always from the DB row, never from a claim.
    tenant_id: uuid.UUID | None
    auth_provider: AuthProvider
    provider_subject: str  # `sub` — the local user id for LOCAL
    claims: Mapping[str, Any]  # verified claims, for audit and /auth/logout
    user: "User | None" = None  # set iff subject_type is USER
    customer: "Customer | None" = None  # set iff subject_type is CUSTOMER
    issued_at: datetime | None = None  # `iat`, for the tokens_valid_from gate

    @property
    def local_id(self) -> uuid.UUID:
        """Primary key of the underlying row, whichever table it lives in."""
        subject = self.user if self.user is not None else self.customer
        if subject is None:  # pragma: no cover - constructed only by the resolver
            msg = "Principal has neither a user nor a customer row"
            raise ValueError(msg)
        return subject.id

    @property
    def is_external(self) -> bool:
        """True when the token came from Supabase or Firebase.

        External sessions cannot be revoked per-device (no server-side session
        handle), so callers that need that distinction — logout especially —
        must branch on it rather than assume the jti blocklist applies.
        """
        return self.auth_provider is not AuthProvider.LOCAL
