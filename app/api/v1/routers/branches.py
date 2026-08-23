"""QuickBite — Branch routes: GET/POST /branches (list, create), QR code
image + rotation, geofence update, fraud-attempt visibility (BRANCH-01).

Delete is a later ticket.
"""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import require_role
from app.core.rbac import RoleLevel
from app.db.base import get_db
from app.db.models.user import User
from app.schemas.branches import (
    BranchCreateRequest,
    BranchGeofenceUpdateRequest,
    BranchOut,
    FraudAttemptOut,
)
from app.services import branch_service

router = APIRouter(prefix="/branches", tags=["branches"])


@router.get("", response_model=list[BranchOut], status_code=status.HTTP_200_OK)
async def list_branches(
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> list[BranchOut]:
    """Manager+ — same rank as `/team` and `/loyalty/analytics`: a Manager
    supervises the floor and needs the branch list, Staff do not."""
    return await branch_service.list_branches(session)


@router.post("", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
async def create_branch(
    body: BranchCreateRequest,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> BranchOut:
    """Owner+ — same rank as inviting a team member: a new branch changes
    what the whole tenant's staff can see and do."""
    return await branch_service.create_branch(session, body, current_user)


@router.get("/{branch_id}/qr-code.png", status_code=status.HTTP_200_OK)
async def get_branch_qr_code(
    branch_id: uuid.UUID,
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Manager+, same rank as the list above — the printable QR for one
    branch, generated on demand (see qr_service's module docstring for why
    this isn't R2-backed yet)."""
    png = await branch_service.get_qr_png(session, branch_id)
    return Response(content=png, media_type="image/png")


@router.post(
    "/{branch_id}/qr-code/regenerate", response_model=BranchOut, status_code=status.HTTP_200_OK
)
async def regenerate_branch_qr_code(
    branch_id: uuid.UUID,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> BranchOut:
    """Owner+ — mints a fresh token, invalidating every receipt already
    printed with the old one. Same rank as create/update on reward programs
    and GMB connect/disconnect: an irreversible, tenant-wide change."""
    return await branch_service.regenerate_qr_token(session, branch_id, current_user)


@router.patch("/{branch_id}/geofence", response_model=BranchOut, status_code=status.HTTP_200_OK)
async def update_branch_geofence(
    branch_id: uuid.UUID,
    body: BranchGeofenceUpdateRequest,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> BranchOut:
    """Owner+ — same rank as create: moving the pin or resizing the radius
    changes who can collect a stamp tenant-wide."""
    return await branch_service.update_branch_geofence(session, branch_id, body, current_user)


@router.get(
    "/{branch_id}/fraud-attempts", response_model=list[FraudAttemptOut], status_code=status.HTTP_200_OK
)
async def list_branch_fraud_attempts(
    branch_id: uuid.UUID,
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> list[FraudAttemptOut]:
    """Manager+, same rank as the branch list — every scan this branch
    rejected for being outside its geofence, newest first."""
    return await branch_service.list_fraud_attempts(session, branch_id)
