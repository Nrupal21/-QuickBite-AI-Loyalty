"""QuickBite — Loyalty business logic: scan processing, stamp logging.

Reward unlock / redemption codes (LOYALTY-04) and fraud-pattern detection
beyond the geofence check (SEC-12/13, anti_fraud.py) are not implemented
yet — see TODOs below.
"""

import hashlib
import uuid

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.db import bootstrap, rls
from app.db.models.branch import Branch
from app.db.models.customer import Customer
from app.db.models.loyalty import StampLog
from app.schemas.loyalty import ScanResponse
from app.services.geofence_service import distance_to_branch_m, is_within_geofence

logger = structlog.get_logger(__name__)

SCAN_RATE_LIMIT_TTL_SECONDS = 3600


class LoyaltyService:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID | None = None):
        self.session = session
        self.tenant_id = tenant_id

    async def process_scan(
        self,
        qr_token: str,
        gps_lat: float,
        gps_lng: float,
        client_ip: str,
        customer: Customer | None = None,
    ) -> ScanResponse:
        branch = await self._get_active_branch(qr_token)

        distance_m = await distance_to_branch_m(self.session, branch, gps_lat, gps_lng)
        within_geofence = is_within_geofence(distance_m, branch.geofence_radius_m)

        rate_limit_identity = customer.phone_hash if customer else hashlib.sha256(client_ip.encode()).hexdigest()
        rate_limit_key = f"stamp:{branch.id}:{rate_limit_identity}"

        if not within_geofence:
            self._add_stamp_log(
                branch=branch,
                customer=customer,
                rate_limit_identity=rate_limit_identity,
                gps_lat=gps_lat,
                gps_lng=gps_lng,
                distance_m=distance_m,
                is_fraudulent=True,
            )
            await self.session.commit()
            logger.info(
                "loyalty.scan.outside_geofence",
                branch_id=str(branch.id),
                tenant_id=str(branch.tenant_id),
                distance_m=distance_m,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "GEOFENCE_OUT_OF_RANGE",
                        "message": "You need to be at the restaurant to collect a stamp.",
                    }
                },
            )

        if await cache_service.exists(rate_limit_key):
            logger.info("loyalty.scan.rate_limited", branch_id=str(branch.id))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": {
                        "code": "GEOFENCE_RATE_LIMIT",
                        "message": "Already collected your stamp today at this location.",
                        "retry_after_seconds": SCAN_RATE_LIMIT_TTL_SECONDS,
                    }
                },
                headers={"Retry-After": str(SCAN_RATE_LIMIT_TTL_SECONDS)},
            )

        self._add_stamp_log(
            branch=branch,
            customer=customer,
            rate_limit_identity=rate_limit_identity,
            gps_lat=gps_lat,
            gps_lng=gps_lng,
            distance_m=distance_m,
            is_fraudulent=False,
        )

        if customer is not None:
            customer.total_stamps_alltime += 1
            customer.current_reward_count += 1

        await self.session.commit()
        await cache_service.set(rate_limit_key, "1", ttl=SCAN_RATE_LIMIT_TTL_SECONDS)

        logger.info(
            "loyalty.scan.success",
            branch_id=str(branch.id),
            tenant_id=str(branch.tenant_id),
            customer_id=str(customer.id) if customer else None,
        )

        # TODO(LOYALTY-04): compute reward_progress/reward_unlocked/redemption_code
        # against the branch's RewardProgram once that model exists.
        stamp_count = customer.current_reward_count if customer else 1
        return ScanResponse(
            stamp_count=stamp_count,
            reward_progress=0.0,
            reward_unlocked=False,
            redemption_code=None,
            next_reward_at=None,
        )

    async def _get_active_branch(self, qr_token: str) -> Branch:
        """Resolve the QR token to a tenant, bind it, then read the branch scoped.

        The scan is anonymous by design (LOYALTY-03), so the token is the only
        credential and `restaurant.branches` is RLS-protected — without the
        bind this returns zero rows and a valid QR code reads as deactivated.
        """
        tenant_id = await bootstrap.tenant_for_branch_qr_token(self.session, qr_token)
        if tenant_id is not None:
            await rls.set_tenant_context(self.session, tenant_id)

        result = await self.session.execute(
            select(Branch).where(Branch.qr_code_token == qr_token, Branch.is_active.is_(True))
        )
        branch = result.scalar_one_or_none()
        if branch is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "LOYALTY_INVALID_QR",
                        "message": "This QR code is no longer active.",
                    }
                },
            )
        return branch

    def _add_stamp_log(
        self,
        branch: Branch,
        customer: Customer | None,
        rate_limit_identity: str,
        gps_lat: float,
        gps_lng: float,
        distance_m: float,
        is_fraudulent: bool,
    ) -> None:
        self.session.add(
            StampLog(
                tenant_id=branch.tenant_id,
                branch_id=branch.id,
                customer_id=customer.id if customer else None,
                customer_phone_hash=rate_limit_identity if customer else None,
                gps_latitude_at_scan=gps_lat,
                gps_longitude_at_scan=gps_lng,
                distance_from_branch_m=distance_m,
                is_fraudulent=is_fraudulent,
            )
        )
