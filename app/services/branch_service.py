"""QuickBite — Branch read model for dashboard pages, plus QR code
generation and rotation (BRANCH-01).

Branch create/update/delete lands with a later Settings ticket — this
service exists now because the Google Profile Link and QR Codes pages both
need to enumerate branches (the former for GMB connection state, the latter
for the printable QR + regenerate action), and that read is reusable as-is
once mutations land alongside it.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_qr_token
from app.db.models.audit import AuditLog
from app.db.models.branch import Branch
from app.db.models.reputation import GMBProfile
from app.db.models.user import User
from app.schemas.branches import BranchOut
from app.services import qr_service


async def list_branches(session: AsyncSession) -> list[BranchOut]:
    """RLS already scopes both tables to the caller's tenant — `app.tenant_id`
    is bound by `require_role` before any route calls this, so there is no
    explicit `tenant_id` filter here (same pattern as reputation.py's
    `_get_owned_branch`). One `GMBProfile` row exists per branch at most
    (`gmb_service.complete_connect` upserts), so the outer join never
    duplicates a branch row."""
    result = await session.execute(
        select(Branch, GMBProfile)
        .outerjoin(GMBProfile, GMBProfile.branch_id == Branch.id)
        .order_by(Branch.name)
    )
    return [_to_response(branch, profile) for branch, profile in result.all()]


async def get_qr_png(session: AsyncSession, branch_id: uuid.UUID) -> bytes:
    branch = await _get_branch_or_404(session, branch_id)
    return qr_service.generate_qr_png(branch.qr_code_token)


async def regenerate_qr_token(
    session: AsyncSession, branch_id: uuid.UUID, owner: User
) -> BranchOut:
    """Mints a fresh token, invalidating every receipt already printed with
    the old one — the QR Codes page's "Regenerate" action, for a leaked or
    compromised code."""
    branch = await _get_branch_or_404(session, branch_id)
    branch.qr_code_token = generate_qr_token()
    session.add(
        AuditLog(
            tenant_id=owner.tenant_id,
            user_id=owner.id,
            action="branch_qr_regenerated",
            resource_type="branch",
            resource_id=branch.id,
        )
    )
    await session.commit()

    result = await session.execute(
        select(GMBProfile).where(GMBProfile.branch_id == branch.id)
    )
    profile = result.scalar_one_or_none()
    return _to_response(branch, profile)


def _to_response(branch: Branch, profile: GMBProfile | None) -> BranchOut:
    return BranchOut(
        id=branch.id,
        name=branch.name,
        is_active=branch.is_active,
        qr_code_token=branch.qr_code_token,
        gmb_connected=bool(profile and profile.is_connected),
        gmb_last_synced_at=profile.last_synced_at if profile else None,
    )


async def _get_branch_or_404(session: AsyncSession, branch_id: uuid.UUID) -> Branch:
    """Same 404-for-both shape as reputation.py's `_get_owned_branch`: RLS
    should already hide another tenant's branch, this is the belt to that
    braces."""
    result = await session.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "BRANCH_NOT_FOUND", "message": "No such branch."}},
        )
    return branch
