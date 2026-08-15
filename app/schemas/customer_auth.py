"""QuickBite — Customer auth schemas: OTPRequest, OTPVerify, CustomerToken.

NEW-OTP-01/02 request/response pairs. CustomerRegister/CustomerProfile live
in schemas/customers.py (NEW-OTP-03 / customer profile tickets).
"""

import uuid

from pydantic import BaseModel, Field


class OTPRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)  # E.164 phone or email
    tenant_id: uuid.UUID


class OTPSentResponse(BaseModel):
    status: str = "sent"


class OTPNewUserResponse(BaseModel):
    status: str = "new_user"
    registration_token: str


class OTPVerify(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)
    tenant_id: uuid.UUID
    otp_code: str = Field(min_length=6, max_length=6)


class OTPVerifiedResponse(BaseModel):
    status: str = "verified"
    customer_id: str
    total_stamps_alltime: int
    current_reward_count: int


class OAuthSignInRequest(BaseModel):
    """Firebase abstracts Google/Apple/Microsoft/GitHub/Twitter into one ID
    token shape, so the backend has exactly one verification path regardless
    of which button the customer clicked — no `provider` field needed."""

    id_token: str = Field(min_length=1)
    tenant_id: uuid.UUID


class OAuthNewUserResponse(BaseModel):
    """No existing identity_links row for this Firebase subject.

    Every Customer row requires a phone number (loyalty_service's geofence
    rate limiter and SMS/WhatsApp alerts both key off phone_hash), and a
    social sign-in never hands one back — so this cannot provision a Customer
    on the spot. `registration_token` is the same shape customer_otp_service
    hands back for a brand-new phone/email; /customers/register finishes the
    job and creates the identity_links row.
    """

    status: str = "new_user"
    registration_token: str
    email: str | None = None  # verified email, if the provider supplied one
