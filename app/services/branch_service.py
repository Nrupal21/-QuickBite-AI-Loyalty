"""QuickBite — Branch read/create model for dashboard pages, plus QR code
generation and rotation (BRANCH-01).

Update/delete are a later ticket. Create exists because every other
dashboard page that reads branches (Google Profile Link, QR Codes, Loyalty
Programs) is a dead end without a way to add the first one — a tenant with
zero branches previously had no path to one except a direct DB insert.
"""

import uuid

from fastapi import HTTPException, status
from geoalchemy2.elements import WKBElement, WKTElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.encryption import encrypt_pii, sha256_hex
from app.core.security import generate_qr_token
from app.db.models.audit import AuditLog
from app.db.models.branch import Branch
from app.db.models.loyalty import StampLog
from app.db.models.reputation import GMBProfile
from app.db.models.user import User
from app.schemas.branches import (
    BranchCreateRequest,
    BranchGeofenceUpdateRequest,
    BranchOut,
    FraudAttemptOut,
)
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


async def create_branch(session: AsyncSession, request: BranchCreateRequest, owner: User) -> BranchOut:
    """Owner+. address is TIER 3 (AGENTS.md §3 restaurant rules): hashed for
    a future lookup index, encrypted for display, never stored plaintext.
    GPS is PostGIS-only — no plaintext latitude/longitude columns, same rule
    the model's own header comment locks for every branch."""
    normalised_address = " ".join(request.address.split()).lower()
    location = (
        from_shape(Point(request.gps_lng, request.gps_lat), srid=4326)
        if settings.POSTGIS_ENABLED
        else f"POINT({request.gps_lng} {request.gps_lat})"
    )

    branch = Branch(
        id=uuid.uuid4(),
        tenant_id=owner.tenant_id,
        name=request.name,
        address_hash=sha256_hex(normalised_address),
        encrypted_address=encrypt_pii(request.address),
        location=location,
        geofence_radius_m=request.geofence_radius_m,
        qr_code_token=generate_qr_token(),
        is_active=True,
    )
    session.add(branch)
    session.add(
        AuditLog(
            tenant_id=owner.tenant_id,
            user_id=owner.id,
            action="branch_created",
            resource_type="branch",
            resource_id=branch.id,
        )
    )
    await session.commit()

    return _to_response(branch, None)


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


async def update_branch_geofence(
    session: AsyncSession, branch_id: uuid.UUID, request: BranchGeofenceUpdateRequest, owner: User
) -> BranchOut:
    """Owner+ — moves the pin and/or resizes the radius on an existing
    branch. Same PostGIS-only storage rule as create_branch: the coordinates
    never touch a plaintext column."""
    branch = await _get_branch_or_404(session, branch_id)
    branch.location = (
        from_shape(Point(request.gps_lng, request.gps_lat), srid=4326)
        if settings.POSTGIS_ENABLED
        else f"POINT({request.gps_lng} {request.gps_lat})"
    )
    branch.geofence_radius_m = request.geofence_radius_m
    session.add(
        AuditLog(
            tenant_id=owner.tenant_id,
            user_id=owner.id,
            action="branch_geofence_updated",
            resource_type="branch",
            resource_id=branch.id,
        )
    )
    await session.commit()

    result = await session.execute(select(GMBProfile).where(GMBProfile.branch_id == branch.id))
    profile = result.scalar_one_or_none()
    return _to_response(branch, profile)


async def list_fraud_attempts(session: AsyncSession, branch_id: uuid.UUID) -> list[FraudAttemptOut]:
    """Manager+ — every scan `loyalty_service.process_scan` rejected with
    GEOFENCE_OUT_OF_RANGE for this branch, newest first. RLS already scopes
    `stamp_logs` to the caller's tenant (same reasoning as list_branches)."""
    branch = await _get_branch_or_404(session, branch_id)
    result = await session.execute(
        select(StampLog)
        .where(StampLog.branch_id == branch_id, StampLog.is_fraudulent.is_(True))
        .order_by(StampLog.scanned_at.desc())
        .limit(50)
    )
    return [
        FraudAttemptOut(
            id=log.id,
            branch_id=log.branch_id,
            branch_name=branch.name,
            distance_m=log.distance_from_branch_m,
            geofence_radius_m=branch.geofence_radius_m,
            is_registered_customer=log.customer_id is not None,
            scanned_at=log.scanned_at,
        )
        for log in result.scalars().all()
    ]


def _extract_lat_lng(location) -> tuple[float, float]:
    """Read the branch's coordinates back out of the PostGIS-only `location`
    column — for the geofence editor's pre-fill, never for a stored column
    (see the Branch model's own header comment). Dispatches on the actual
    runtime type rather than `settings.POSTGIS_ENABLED`: a real PostGIS read
    hydrates a WKBElement regardless of that flag, while the local-dev Text
    fallback (and bare Branch objects built directly in tests) hold a plain
    WKT string."""
    if isinstance(location, (WKBElement, WKTElement)):
        point = to_shape(location)
        return point.y, point.x
    lng_str, lat_str = str(location).removeprefix("POINT(").removesuffix(")").split()
    return float(lat_str), float(lng_str)


def _to_response(branch: Branch, profile: GMBProfile | None) -> BranchOut:
    gps_lat, gps_lng = _extract_lat_lng(branch.location)
    return BranchOut(
        id=branch.id,
        name=branch.name,
        is_active=branch.is_active,
        qr_code_token=branch.qr_code_token,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        geofence_radius_m=branch.geofence_radius_m,
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
