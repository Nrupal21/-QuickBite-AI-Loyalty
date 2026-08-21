"""QuickBite — Inbound GMB review sync (REVIEW-03).

Pulls new reviews for a connected `GMBProfile` and stores them as
`CustomerReview` rows, so REVIEW-01's composer and REVIEW-02's approval
workflow have real Google reviews to draft against. `gmb_service` owns the
HTTP client; this module owns what happens with what it returns.

Dedupe strategy: `fetch_reviews` returns pages newest-first (Google's
default order for this endpoint), so a sync walks pages only until it hits
either a review id already stored for the branch or `gmb_sync_cursor` — the
newest review id seen on the previous run — whichever comes first. That
bounds every run to just what changed since last time without needing a
server-side "since" filter, which the GMB API does not offer.
"""

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import rls
from app.db.models.reputation import CustomerReview, GMBProfile
from app.db.models.tenant import Tenant
from app.services import gmb_service
from app.services.gmb_service import GMBNotConnected, GMBTokenExpired

logger = structlog.get_logger(__name__)

_STAR_RATING = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}

# Bounds one sync run against an unbounded backlog (e.g. a branch's first-ever
# connect with years of history) the same way response_service.BATCH_SIZE
# bounds the hourly draft sweep.
_MAX_PAGES_PER_SYNC = 10


def _parse_gmb_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _build_review(profile: GMBProfile, raw: dict) -> CustomerReview:
    reviewer = raw.get("reviewer") or {}
    created_at = raw.get("createTime")
    return CustomerReview(
        tenant_id=profile.tenant_id,
        branch_id=profile.branch_id,
        source="google",
        external_review_id=raw["reviewId"],
        rating=_STAR_RATING.get(raw.get("starRating", ""), 0),
        reviewer_name=reviewer.get("displayName"),
        review_body=raw.get("comment"),
        reviewed_at=_parse_gmb_timestamp(created_at) if created_at else datetime.now(timezone.utc),
    )


async def sync_profile(session: AsyncSession, profile: GMBProfile) -> int:
    """Pull and store every review newer than the branch's last sync.

    Returns the count of new reviews stored. Any GMB auth failure (not
    connected, refresh failed) is caught and returns 0 rather than raising —
    a sweep across many branches must not let one broken profile abort the
    rest, and `get_valid_access_token` has already flipped `is_connected` for
    the revoked case.
    """
    try:
        access_token = await gmb_service.get_valid_access_token(session, profile)
    except (GMBNotConnected, GMBTokenExpired):
        logger.warning(
            "gmb.sync.skipped_no_token", branch_id=str(profile.branch_id), tenant_id=str(profile.tenant_id)
        )
        return 0

    known_ids = set(
        (
            await session.execute(
                select(CustomerReview.external_review_id).where(
                    CustomerReview.branch_id == profile.branch_id,
                    CustomerReview.source == "google",
                )
            )
        )
        .scalars()
        .all()
    )

    stored = 0
    newest_seen_id: str | None = None
    page_token: str | None = None

    for _ in range(_MAX_PAGES_PER_SYNC):
        payload = await gmb_service.fetch_reviews(
            access_token, profile.gmb_account_id, profile.gmb_location_id, page_token
        )
        reached_known = False

        for raw in payload.get("reviews", []):
            review_id = raw.get("reviewId")
            if not review_id:
                continue
            if newest_seen_id is None:
                newest_seen_id = review_id
            if review_id == profile.gmb_sync_cursor or review_id in known_ids:
                reached_known = True
                break
            session.add(_build_review(profile, raw))
            known_ids.add(review_id)
            stored += 1

        if reached_known:
            break

        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    if newest_seen_id is not None:
        profile.gmb_sync_cursor = newest_seen_id
    profile.last_synced_at = datetime.now(timezone.utc)
    await session.commit()

    logger.info(
        "gmb.sync.done", branch_id=str(profile.branch_id), tenant_id=str(profile.tenant_id), stored=stored
    )
    return stored


async def sync_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Sync every connected GMB profile for one tenant.

    Used by `sync_gmb_tenant` (ADMIN-01's `POST /admin/tenants/{id}/sync-gmb`).
    Runs inside `rls.tenant_context` since the admin route calls this from
    the BYPASSRLS session — profile rows must still be read tenant-scoped.
    """
    async with rls.tenant_context(session, tenant_id):
        profiles = (
            await session.execute(
                select(GMBProfile).where(
                    GMBProfile.tenant_id == tenant_id, GMBProfile.is_connected.is_(True)
                )
            )
        ).scalars().all()

        total = 0
        for profile in profiles:
            total += await sync_profile(session, profile)
        return total


async def sync_all_connected(session: AsyncSession) -> int:
    """Sweep every tenant's connected GMB profiles. Runs on Celery beat.

    Walks tenants explicitly the same way `response_service.
    generate_pending_response_drafts` does — no authenticated principal to
    bind tenant context from, since this has no request at all.
    """
    tenant_ids = (await session.execute(select(Tenant.id))).scalars().all()

    total = 0
    for tenant_id in tenant_ids:
        total += await sync_tenant(session, tenant_id)

    logger.info("gmb.sync.sweep_done", tenants_checked=len(tenant_ids), reviews_stored=total)
    return total
