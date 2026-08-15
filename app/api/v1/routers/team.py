"""QuickBite — Team management routes: invite, accept, list, deactivate (AUTH-04).

These are the first routes to actually use `require_role()`, and they map
straight onto Doc 3's permission matrix:

  invite / deactivate  -> OWNER  ("invite / remove team members": OWNER, SUPER)
  list team            -> MANAGER (a Manager supervises the floor; Staff do not)
  accept invite        -> public  (the invitee has no account to authenticate with)

`require_role(RoleLevel.OWNER)` admits Super Admin too, since Role.level ranks
1=Super .. 4=Staff and the guard is "at least this senior".
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import require_role
from app.core.rate_limiter import limiter
from app.core.rbac import RoleLevel
from app.db.base import get_db
from app.db.models.user import User
from app.schemas.team import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    DeactivateMemberResponse,
    StaffInviteRequest,
    StaffInviteResponse,
    TeamListResponse,
)
from app.services.team_service import TeamService

router = APIRouter(prefix="/team", tags=["team"])


@router.post("/invite", response_model=StaffInviteResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/hour")
async def invite_member(
    request: Request,
    payload: StaffInviteRequest,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> StaffInviteResponse:
    # TODO(STITCH-13): the invite email links here, but no page serves
    # GET /team/accept-invite yet — the screen that collects name + password and
    # POSTs to this router is a Stitch ticket (AGENTS.md §8 forbids hand-writing
    # full HTML pages). Until it lands, the API below is reachable only directly.
    accept_base_url = str(request.base_url) + "team/accept-invite"
    return await TeamService(session=session).invite(payload, current_user, accept_base_url)


@router.post(
    "/accept-invite", response_model=AcceptInviteResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("10/hour")
async def accept_invite(
    request: Request,
    payload: AcceptInviteRequest,
    session: AsyncSession = Depends(get_db),
) -> AcceptInviteResponse:
    """Public by necessity — the invitee has no credentials yet. The opaque
    256-bit token from the invite email is the only thing authorising this."""
    return await TeamService(session=session).accept_invite(payload)


@router.get("", response_model=TeamListResponse, status_code=status.HTTP_200_OK)
async def list_members(
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> TeamListResponse:
    return await TeamService(session=session).list_members(current_user)


@router.delete(
    "/{user_id}", response_model=DeactivateMemberResponse, status_code=status.HTTP_200_OK
)
async def deactivate_member(
    user_id: uuid.UUID,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> DeactivateMemberResponse:
    """Soft delete: audit_logs.user_id FKs onto this row, so it is never DELETEd."""
    return await TeamService(session=session).deactivate(user_id, current_user)
