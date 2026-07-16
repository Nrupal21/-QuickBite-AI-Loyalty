"""QuickBite — Auth schemas: UserRegister, UserLogin, TokenResponse, MFAVerify.

AUTH-01 implements the registration pair. Login/MFA/token schemas arrive
with AUTH-02/AUTH-03.
"""

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    restaurant_name: str = Field(min_length=1, max_length=100)


class RegisterResponse(BaseModel):
    status: str  # always "verification_email_sent"


class VerifyEmailResponse(BaseModel):
    status: str  # "verified"
    subdomain: str
