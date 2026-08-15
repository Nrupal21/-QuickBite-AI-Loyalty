"""Schemas for explicit external-identity linking (POST /auth/link/{provider}).

Only the explicit path is exposed here — the request must already carry a
valid local session (`Depends(get_current_user)`), so linking proves control
of the external account on top of an already-proven local identity, rather
than trusting either credential alone.
"""

from typing import Literal

from pydantic import BaseModel, Field


class LinkIdentityRequest(BaseModel):
    """`external_token` is the raw Supabase/Firebase token — verified inside
    the service, never trusted as-is."""

    external_token: str = Field(min_length=1)


class LinkIdentityResponse(BaseModel):
    status: Literal["linked"] = "linked"
    provider: str
