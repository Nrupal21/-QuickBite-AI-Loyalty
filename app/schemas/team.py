"""QuickBite — Team management schemas: staff invite, accept, list, deactivate.

AUTH-04 needs these because until now every account created by
`AuthService.verify_email()` was an OWNER — there was no path to a MANAGER or
STAFF user, so the role guards had nothing to guard against. Doc 3's
permission matrix grants "invite / remove team members" to OWNER only.

An invite is the mirror image of AUTH-01's registration: the pending row lives
in Redis (keyed by an opaque token) rather than the DB, so an abandoned invite
expires on its own and never leaves a half-built User row behind.
"""

import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class StaffInviteRequest(BaseModel):
    """`role` is a Literal, not a free string — it is the privilege-escalation
    boundary. Pydantic rejects "OWNER"/"SUPER_ADMIN" with a 422 before the
    service runs, and team_service re-checks against INVITABLE_ROLE_NAMES so
    the guarantee does not rest on the schema alone."""

    email: EmailStr
    role: Literal["MANAGER", "STAFF"]


class StaffInviteResponse(BaseModel):
    status: str  # always "invite_sent"
    email: EmailStr
    role: str
    expires_in: int  # seconds


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    username: str | None = Field(
        default=None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_.]+$"
    )


class AcceptInviteResponse(BaseModel):
    status: str  # always "account_created"
    role: str
    mfa_required: bool


class TeamMemberResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    username: str | None = None
    role: str
    role_level: int
    mfa_enabled: bool
    email_verified: bool
    is_active: bool


class TeamListResponse(BaseModel):
    members: list[TeamMemberResponse]


class DeactivateMemberResponse(BaseModel):
    status: str  # always "deactivated"
    user_id: uuid.UUID
