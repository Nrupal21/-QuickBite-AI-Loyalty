"""QuickBite — Branch schemas: GET /branches (list), POST /branches (create),
PATCH /branches/{id}/geofence (update), GET /branches/{id}/fraud-attempts.

Powers the Google Profile Link, QR Codes, and Settings dashboard pages.
Delete is a later ticket.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class BranchCreateRequest(BaseModel):
    """POST /branches. Owner+ only.

    `address` and `gps_lat`/`gps_lng` arrive as plaintext from the form —
    branch_service is what applies AGENTS.md's TIER 3 handling (hash +
    encrypt the address, PostGIS-only storage for the coordinates, never a
    plaintext lat/lng column)."""

    name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500)
    gps_lat: float = Field(ge=-90.0, le=90.0)
    gps_lng: float = Field(ge=-180.0, le=180.0)
    geofence_radius_m: int = Field(default=100, ge=10, le=1000)


class BranchGeofenceUpdateRequest(BaseModel):
    """PATCH /branches/{id}/geofence. Owner+ only — same rank as create,
    since moving the pin or widening the radius changes who can collect a
    stamp tenant-wide. Address/name are untouched; this is geofence-only."""

    gps_lat: float = Field(ge=-90.0, le=90.0)
    gps_lng: float = Field(ge=-180.0, le=180.0)
    geofence_radius_m: int = Field(ge=10, le=1000)


class BranchOut(BaseModel):
    """One `restaurant.branches` row, joined with its `google_business_profiles`
    connection state (if any) — the Google Profile Link page's one query.

    `qr_code_token` is not a secret: it is printed on the branch's receipts
    for any diner to scan (loyalty_service._get_active_branch,
    review_service's equivalent lookup), so returning it to any Manager+
    caller who can already see the branch discloses nothing new — it is what
    the QR Codes page's manual-entry fallback and regenerate confirmation
    display.

    `gps_lat`/`gps_lng` are read back out of the PostGIS `location` column
    (never a stored plaintext column, per the model's own rule) purely so the
    Settings geofence editor can pre-fill its form with the branch's current
    pin instead of asking an Owner to redo "Use my current location" just to
    change the radius.
    """

    id: uuid.UUID
    name: str
    is_active: bool
    qr_code_token: str
    gps_lat: float
    gps_lng: float
    geofence_radius_m: int
    gmb_connected: bool
    gmb_last_synced_at: datetime | None


class FraudAttemptOut(BaseModel):
    """One out-of-geofence StampLog row — a scan attempt rejected with
    GEOFENCE_OUT_OF_RANGE. No PII: `is_registered_customer` says whether the
    scanner was a known loyalty member without exposing which one (phone/
    name stay TIER 3 — this is an abuse-pattern signal, not a customer
    lookup)."""

    id: uuid.UUID
    branch_id: uuid.UUID
    branch_name: str
    distance_m: float
    geofence_radius_m: int
    is_registered_customer: bool
    scanned_at: datetime
