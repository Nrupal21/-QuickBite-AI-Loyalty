"""QuickBite — Customer schemas: CustomerRegister, CustomerProfile, CustomerUpdate.

NEW-OTP-03 implements the registration pair. CustomerProfile/CustomerUpdate
belong to the customer profile tickets and are not implemented yet.
"""

from pydantic import BaseModel, EmailStr, Field


class CustomerRegister(BaseModel):
    registration_token: str
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr | None = None
    # Required only when the identify/OTP step used an email identifier —
    # Customer.phone_hash is mandatory, so a phone number must exist either
    # way (E.164). Optional here because the phone-identifier path already
    # has one from the pending registration session.
    phone: str | None = Field(default=None, min_length=8, max_length=20)
    whatsapp_opt_in: bool = False
    # Optional third login identifier, checked by /auth/identify alongside
    # phone/email.
    username: str | None = Field(default=None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_.]+$")


class CustomerRegisterResponse(BaseModel):
    status: str = "registered"
    customer_id: str
