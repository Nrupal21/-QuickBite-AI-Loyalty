"""QuickBite — Loyalty business logic: scan processing, stamp logging,
reward programs + redemption codes (LOYALTY-04).

Fraud-pattern detection beyond the geofence check (SEC-12/13, anti_fraud.py)
is not implemented yet.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.subscription import tenant_has_feature
from app.core import broadcast, cache_service
from app.core.encryption import decrypt_pii
from app.core.security import generate_redemption_code
from app.db import bootstrap, rls
from app.db.models.audit import AuditLog
from app.db.models.branch import Branch
from app.db.models.customer import Customer
from app.db.models.loyalty import RewardProgram, RewardRedemption, StampLog
from app.db.models.user import User
from app.schemas.loyalty import (
    RedeemCodeResponse,
    RewardProgramCreateRequest,
    RewardProgramResponse,
    ScanResponse,
)
from app.services import messaging_service
from app.services.geofence_service import distance_to_branch_m, is_within_geofence

logger = structlog.get_logger(__name__)

SCAN_RATE_LIMIT_TTL_SECONDS = 3600
# LOYALTY-04's WhatsApp reward alert requires this feature key on the
# tenant's plan — matches check_subscription_tier's feature_limits example
# ("multi_branch"/"whatsapp") in subscription.py's docstring.
WHATSAPP_FEATURE = "whatsapp"


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

        # Reward-program lookup and redemption-code creation must land in the
        # same commit as the stamp log and counter bump above — doing this
        # after commit (the shape the LOYALTY-04 TODO originally left it in)
        # would let a crash between the two commits award a stamp with no
        # corresponding code, or a code with no stamp to justify it.
        response, redemption = await self._apply_reward_program(branch, customer)

        await self.session.commit()
        await cache_service.set(rate_limit_key, "1", ttl=SCAN_RATE_LIMIT_TTL_SECONDS)

        logger.info(
            "loyalty.scan.success",
            branch_id=str(branch.id),
            tenant_id=str(branch.tenant_id),
            customer_id=str(customer.id) if customer else None,
            reward_unlocked=response.reward_unlocked,
        )

        if redemption is not None and customer is not None and customer.whatsapp_opt_in:
            await self._send_reward_whatsapp_alert(branch, customer, redemption)

        # DASH-01: nudges an open dashboard tab to re-poll /dashboard/stats.
        # Only the success path publishes — a fraudulent or rate-limited scan
        # returns before this line, so it never fires "new_scan".
        await broadcast.publish_event(
            branch.tenant_id,
            "new_scan",
            {
                "branch_id": str(branch.id),
                "reward_unlocked": response.reward_unlocked,
            },
        )

        return response

    async def _apply_reward_program(
        self, branch: Branch, customer: Customer | None
    ) -> tuple[ScanResponse, RewardRedemption | None]:
        """Check the branch's active RewardProgram against the customer's
        updated stamp count. Anonymous scans (no OTP session) have nowhere to
        track progress — `current_reward_count` lives on Customer — so they
        always read back the same zeroed response the caller had before
        LOYALTY-04. Returns the new `RewardRedemption` too (not just the
        public ScanResponse) so the caller can send the WhatsApp alert with
        the exact `expires_at` that was persisted, not a recomputed one."""
        if customer is None:
            return ScanResponse(stamp_count=1, reward_progress=0.0, reward_unlocked=False), None

        result = await self.session.execute(
            select(RewardProgram)
            .where(RewardProgram.branch_id == branch.id, RewardProgram.is_active.is_(True))
            .limit(1)
        )
        program = result.scalar_one_or_none()
        if program is None:
            return (
                ScanResponse(
                    stamp_count=customer.current_reward_count,
                    reward_progress=0.0,
                    reward_unlocked=False,
                ),
                None,
            )

        stamp_count = customer.current_reward_count
        if stamp_count < program.stamps_required:
            return (
                ScanResponse(
                    stamp_count=stamp_count,
                    reward_progress=stamp_count / program.stamps_required,
                    reward_unlocked=False,
                    next_reward_at=program.stamps_required - stamp_count,
                ),
                None,
            )

        redemption = RewardRedemption(
            tenant_id=branch.tenant_id,
            branch_id=branch.id,
            reward_program_id=program.id,
            customer_id=customer.id,
            code=generate_redemption_code(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=program.validity_days),
        )
        self.session.add(redemption)
        # New cycle starts fresh — this scan's stamp still counted toward the
        # reward just unlocked, so the reset happens after reading stamp_count.
        customer.current_reward_count = 0

        return (
            ScanResponse(
                stamp_count=stamp_count,
                reward_progress=1.0,
                reward_unlocked=True,
                redemption_code=redemption.code,
                validity_days=program.validity_days,
            ),
            redemption,
        )

    async def _send_reward_whatsapp_alert(
        self, branch: Branch, customer: Customer, redemption: RewardRedemption
    ) -> None:
        if not await tenant_has_feature(self.session, branch.tenant_id, WHATSAPP_FEATURE):
            return
        await messaging_service.send_reward_unlocked_whatsapp(
            decrypt_pii(customer.encrypted_phone),
            branch.name,
            redemption.code,
            redemption.expires_at.strftime("%d %b %Y"),
            session=self.session,
        )

    async def create_reward_program(
        self, request: RewardProgramCreateRequest, owner: User
    ) -> RewardProgramResponse:
        """Owner-configured loyalty rule for one of their branches. `branch_id`
        is trusted only after this lookup confirms it — RLS already scopes the
        query to `owner.tenant_id`, so a branch belonging to another tenant
        reads back as not found rather than a cross-tenant 403 that would
        confirm the id exists."""
        result = await self.session.execute(
            select(Branch.id).where(Branch.id == request.branch_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {"code": "BRANCH_NOT_FOUND", "message": "No such branch."}
                },
            )

        program = RewardProgram(
            # id/is_active set explicitly, not left to the column defaults —
            # those only apply at flush, and the response below reads the
            # object's own attributes straight back, same as team_service's
            # User(id=uuid.uuid4(), ...) on account creation.
            id=uuid.uuid4(),
            tenant_id=owner.tenant_id,
            branch_id=request.branch_id,
            name=request.name,
            stamps_required=request.stamps_required,
            reward_type=request.reward_type,
            reward_value=request.reward_value,
            validity_days=request.validity_days,
            is_active=True,
        )
        self.session.add(program)
        await self.session.commit()

        logger.info(
            "loyalty.reward_program.created",
            tenant_id=str(owner.tenant_id),
            branch_id=str(request.branch_id),
        )
        return RewardProgramResponse(
            id=program.id,
            branch_id=program.branch_id,
            name=program.name,
            stamps_required=program.stamps_required,
            reward_type=program.reward_type,
            reward_value=program.reward_value,
            validity_days=program.validity_days,
            is_active=program.is_active,
        )

    async def redeem_code(self, code: str, staff: User) -> RedeemCodeResponse:
        """Staff-facing verification at the till. RLS already scopes the
        lookup to the staff member's own tenant, so a code from another
        tenant reads back as not found — the same 404-for-both shape team_service
        uses for a team member in another tenant."""
        result = await self.session.execute(
            select(RewardRedemption).where(RewardRedemption.code == code)
        )
        redemption = result.scalar_one_or_none()
        if redemption is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "REDEMPTION_CODE_NOT_FOUND",
                        "message": "No such redemption code.",
                    }
                },
            )

        if redemption.redeemed_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "REDEMPTION_CODE_ALREADY_USED",
                        "message": "This code has already been redeemed.",
                    }
                },
            )

        if redemption.expires_at < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={
                    "error": {
                        "code": "REDEMPTION_CODE_EXPIRED",
                        "message": "This code has expired.",
                    }
                },
            )

        redemption.redeemed_at = datetime.now(timezone.utc)
        redemption.redeemed_by_user_id = staff.id
        self.session.add(
            AuditLog(
                tenant_id=staff.tenant_id,
                user_id=staff.id,
                action="reward_redeemed",
                resource_type="reward_redemption",
                resource_id=redemption.id,
                event_metadata={"code": redemption.code},
            )
        )
        await self.session.commit()

        logger.info(
            "loyalty.reward.redeemed",
            tenant_id=str(staff.tenant_id),
            redemption_id=str(redemption.id),
        )
        return RedeemCodeResponse(
            status="redeemed", code=redemption.code, customer_id=redemption.customer_id
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
