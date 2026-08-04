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
