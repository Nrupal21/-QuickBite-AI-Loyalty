"""QuickBite — Auth schemas: UserRegister, UserLogin, TokenResponse, MFAVerify.

AUTH-01 implements the registration pair. AUTH-02/AUTH-03 add the login,
MFA, refresh, and logout schemas below. IdentifyRequest/IdentifyResponse
back the unified identify-first login entry point (checks both the
Owner/Staff and Customer domains before either flow runs).

AUTH-04 adds MeResponse (the role/permission payload the dashboard reads to
decide what to render) and the MFA-enrollment trio, which exists because
Doc 3 marks MFA REQUIRED for Super Admin, Owner, and Manager: login now
refuses to issue a token pair to those roles until a TOTP secret is
enrolled, so there has to be a way to enroll one.
"""

import uuid

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    restaurant_name: str = Field(min_length=1, max_length=100)
    # Optional third login identifier, checked by /auth/identify alongside
    # email/phone. Not required — existing registrations are unaffected.
    username: str | None = Field(default=None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_.]+$")


class RegisterResponse(BaseModel):
    status: str  # always "verification_email_sent"


class VerifyEmailResponse(BaseModel):
    status: str  # "verified"
    subdomain: str


class UserLogin(BaseModel):
    """Accepts whatever identifier /auth/identify just matched on.

    /auth/identify resolves an account by email OR username, so login has to
    accept both or the identify-first screen dead-ends for anyone who typed
    their username. Shape validation lives in classify_staff_identifier() —
    an EmailStr here would reject usernames outright.

    Phone is deliberately not a staff login identifier: `users.phone_hash` is
    indexed but NOT unique (unlike email_hash/username_hash), so a phone can
    match several accounts and there is no tenant context on this endpoint to
    disambiguate. Staff phone stays a contact/recovery field only.
    """

    identifier: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class MFAChallengeResponse(BaseModel):
    status: str  # always "mfa_required"
    mfa_session_token: str
    expires_in: int  # seconds


class MFAEnrollmentRequiredResponse(BaseModel):
    """Password was correct, but the role requires MFA and none is enrolled.

    Carries the same short-lived `mfa_session_token` as the challenge above —
    it is a limited credential that can ONLY drive /auth/mfa/enroll and
    /auth/mfa/confirm, never a substitute for an access token.
    """

    status: str  # always "mfa_enrollment_required"
    mfa_session_token: str
    expires_in: int  # seconds
    role: str


class MFAStartRequest(BaseModel):
    """Voluntary enrollment by an already-authenticated user (e.g. Staff, whose
    role makes MFA optional). Body is empty — the bearer token identifies them."""


class MFAStartResponse(BaseModel):
    mfa_session_token: str
    expires_in: int  # seconds


class MFAEnrollRequest(BaseModel):
    mfa_session_token: str


class MFAEnrollResponse(BaseModel):
    """`secret` is shown once for manual entry; `provisioning_uri` renders the QR.

    Nothing is written to the User row here — the secret stays in Redis until
    /auth/mfa/confirm proves the authenticator actually holds it, so a
    half-finished enrollment can never lock someone out of their account.
    """

    secret: str
    provisioning_uri: str
    expires_in: int  # seconds


class MFAConfirmRequest(BaseModel):
    mfa_session_token: str
    totp_code: str = Field(min_length=6, max_length=6)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int  # seconds
    role: str
    tenant_id: str


class MFAVerify(BaseModel):
    mfa_session_token: str
    totp_code: str = Field(min_length=6, max_length=6)


class RefreshRequest(BaseModel):
    refresh_token: str


class StatusResponse(BaseModel):
    status: str


class IdentifyRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)
    tenant_id: uuid.UUID


class IdentifyResponse(BaseModel):
    found: bool
    account_type: str | None = None  # "staff" | "customer" | None
    available_methods: list[str] = Field(default_factory=list)  # ["password"] | ["otp"] | []


class MeResponse(BaseModel):
    """GET /auth/me — who the bearer token belongs to and what they may do.

    The dashboard reads `permissions` (static.roles.permissions JSONB) to decide
    which controls to render. That is a UI convenience only: every gated route
    still enforces require_role() server-side, because a client can send any
    request it likes regardless of what it chose to draw.
    """

    user_id: str
    tenant_id: str
    email: EmailStr
    username: str | None = None
    role: str
    role_level: int
    permissions: dict = Field(default_factory=dict)
    mfa_enabled: bool
    mfa_required: bool
    email_verified: bool
