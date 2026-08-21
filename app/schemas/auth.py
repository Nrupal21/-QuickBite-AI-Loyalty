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
    """Registers a standard, tenant-less account (role USER). Restaurant
    registration is a separate, later step — see BecomeRestaurantRequest."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    # Optional third login identifier, checked by /auth/identify alongside
    # email/phone. Not required — existing registrations are unaffected.
    username: str | None = Field(default=None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_.]+$")


class RegisterResponse(BaseModel):
    status: str  # always "verification_email_sent"


class TenantLookupResponse(BaseModel):
    """Resolves a restaurant's public subdomain to the tenant_id the
    identify-first login screen needs. Both `subdomain` and `name` are TIER 1
    plaintext (see AGENTS.md's field classification) — same visibility as a
    company name on a storefront, not PII."""

    found: bool
    tenant_id: str | None = None
    name: str | None = None


class VerifyEmailResponse(BaseModel):
    """Verifying the email only activates the standard-user account now — no
    Tenant exists yet, so there is no subdomain until become_restaurant()."""

    status: str  # "verified"
    subdomain: str | None = None


class BecomeRestaurantRequest(BaseModel):
    """POST /auth/register-restaurant — a standard user's one-time upgrade to
    Owner. plan_id is validated (exists + active) here so a bad id fails
    before any Tenant is created, but checkout itself is a separate,
    subsequent call to POST /billing/checkout with the freshly-issued OWNER
    token — keeps the Razorpay round trip out of this transaction.

    category_id comes before plan_id in the "Join Us" flow and is required,
    not optional: the plan list the caller chose from was filtered by this
    category (GET /billing/plans?category_id=), so accepting a submission
    without one would record a tenant whose plan nobody can explain.

    contact_verification_token proves the caller's *second* contact method
    was just OTP-verified (see ContactOtpVerifyResponse below). Which method
    that is depends on how they signed up: a user who registered with an
    email verifies a phone here, and one who registered with a phone verifies
    an email. Either way the account ends up with both, which is what the
    Owner notifications (join confirmation, reward alerts, lockout warnings)
    need."""

    restaurant_name: str = Field(min_length=1, max_length=100)
    category_id: uuid.UUID
    plan_id: uuid.UUID
    contact_verification_token: str


class BecomeRestaurantResponse(BaseModel):
    status: str  # always "restaurant_created"
    tenant_id: str
    subdomain: str
    access_token: str
    refresh_token: str
    expires_in: int  # seconds
    role: str  # always "OWNER"


class ContactOtpRequestRequest(BaseModel):
    """POST /auth/register-restaurant/contact-otp/request — the "Join Us"
    flow's second-contact-method step. Authenticated (bearer token identifies
    the USER), so only the contact itself travels in the body.

    `contact` is an email address or an E.164 phone number; the service
    classifies it and picks the delivery channel, rather than the client
    declaring which it sent. A client that gets to name the channel is a
    client that can ask for an SMS to be sent to an address that is really an
    email, and vice versa."""

    contact: str = Field(min_length=3, max_length=254)


class ContactOtpRequestResponse(BaseModel):
    status: str  # always "otp_sent"
    channel: str  # "email" | "sms" — what the UI tells the user to go check


class ContactOtpVerifyRequest(BaseModel):
    contact: str = Field(min_length=3, max_length=254)
    otp_code: str = Field(min_length=6, max_length=6)


class ContactOtpVerifyResponse(BaseModel):
    """`contact_verification_token` is single-use and short-lived — it proves
    to become_restaurant() that this contact was just OTP-verified for this
    user, without making the caller resend the OTP a second time."""

    contact_verification_token: str
    expires_in: int  # seconds


# --- Unified sign-in: OTP and OAuth as first-class registration methods ---
#
# The login screen offers three routes to the same account — password, OTP,
# and social — and registration accepts all three too. These schemas back the
# OTP and OAuth pair; the password route keeps UserRegister/UserLogin below.
#
# The deliberate asymmetry: /auth/otp/request NEVER reveals whether the
# identifier belongs to an account. It answers "otp_sent" either way, so the
# endpoint cannot be walked to enumerate registered users — the same rule
# AGENTS.md states for the customer OTP flow. The account-exists answer comes
# from /auth/otp/verify, which is reachable only by someone who already read
# the code out of that inbox or handset, i.e. the account's owner.


class UserOtpRequestRequest(BaseModel):
    """`identifier` is an email address or an E.164 phone number. The service
    classifies it — the client never declares which kind it sent, because a
    client that names the channel can ask for an SMS to an email address."""

    identifier: str = Field(min_length=3, max_length=254)


class UserOtpSentResponse(BaseModel):
    """Identical whether or not the identifier matched an account.

    `channel` is derived from the identifier's own shape, not from account
    state, so it leaks nothing: anyone typing an email already knows an email
    is what gets a code.
    """

    status: str = "otp_sent"
    channel: str  # "email" | "sms"
    expires_in: int  # seconds


class UserOtpVerifyRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)
    otp_code: str = Field(min_length=6, max_length=6)


class OtpRegistrationRequiredResponse(BaseModel):
    """The code was correct, but no account owns this identifier yet.

    Only a caller holding the OTP reaches this, so it is not an enumeration
    oracle. `registration_token` carries the now-proven identifier server-side
    — the completion call sends only the name and optional password, so a
    tampered client cannot swap in an identifier it never verified.
    """

    status: str = "registration_required"
    registration_token: str
    identifier: str  # echoed back so the form can show what it is completing
    identifier_type: str  # "email" | "phone"
    expires_in: int  # seconds


class CompleteOtpRegistrationRequest(BaseModel):
    """Finishes an OTP sign-up. `password` is optional by design: the plan is
    "everyone registers as a normal user, and may add a password if they
    prefer one". Omitting it produces an account with hashed_password NULL
    that signs in by OTP or social only — see migration 0014."""

    registration_token: str
    name: str = Field(min_length=1, max_length=100)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserOAuthSignInRequest(BaseModel):
    """Firebase abstracts Google/Apple/Microsoft/GitHub/Twitter into one ID
    token shape, so there is one verification path here regardless of which
    button was pressed — no `provider` field, same as the customer flow."""

    id_token: str = Field(min_length=1)


class OAuthRegistrationRequiredResponse(BaseModel):
    """No account is linked to this external subject yet.

    A social sign-in gives a verified email but no phone and no chosen
    password, so it cannot provision an account outright — the caller
    confirms their display name first. `email` is populated only when the
    provider marked it verified; an unverified email is that provider's
    unbacked claim about someone else's address, and joining on it would let
    an attacker inherit an account by registering its address upstream.
    """

    status: str = "registration_required"
    registration_token: str
    email: str | None = None
    suggested_name: str | None = None
    expires_in: int  # seconds


class CompleteOAuthRegistrationRequest(BaseModel):
    registration_token: str
    name: str = Field(min_length=1, max_length=100)


class ForgotPasswordRequest(BaseModel):
    """Same identifier acceptance as UserLogin — email or username."""

    identifier: str = Field(min_length=3, max_length=254)


class ForgotPasswordResponse(BaseModel):
    """Always the same value regardless of whether the identifier matched an
    account — an enumeration oracle here would let an attacker map out
    registered emails/usernames one guess at a time."""

    status: str  # always "if_registered_email_sent"


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class ResetPasswordResponse(BaseModel):
    status: str  # always "password_reset"


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
    # None for a standard user (role USER) — no restaurant registered yet.
    tenant_id: str | None = None


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
    # None for a standard user (role USER) — no restaurant registered yet.
    tenant_id: str | None = None
    # None for an account registered by phone OTP, which has no email until
    # "Join Us" verifies one (migration 0014). The "Join Us" form reads both
    # fields to decide which one to pre-fill and which one to ask for.
    email: EmailStr | None = None
    phone: str | None = None
    username: str | None = None
    role: str
    role_level: int
    permissions: dict = Field(default_factory=dict)
    mfa_enabled: bool
    mfa_required: bool
    email_verified: bool
