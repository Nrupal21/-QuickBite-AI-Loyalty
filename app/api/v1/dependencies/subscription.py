"""QuickBite — Subscription dependencies: check_subscription_tier().

AUTH-04's third guard. Gates a route on whether the caller's tenant plan
grants a named feature. Reads the tenant's current plan straight off
`Tenant.plan_id` rather than the payment-schema `Subscription` row —
SUB-01 (Stripe checkout, `Subscription` row creation) isn't built yet, and
`Tenant.plan_id` is the simpler, already-populated source of the tenant's
active plan for gating purposes. Usage-counter tracking
(`track_usage()`/`check_plan_limit()` against `UsageTracking`) is SUB-02
and not implemented here — this only checks the plan's `feature_limits`
JSONB, not consumption against a monthly quota.
"""

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import get_current_user
from app.db.base import get_db
from app.db.models.subscription import SubscriptionPlan
from app.db.models.tenant import Tenant
from app.db.models.user import User


def check_subscription_tier(feature: str):
    """Dependency factory — 402 unless the caller's tenant plan grants `feature`.

    `feature_limits` is a JSONB map (e.g. `{"multi_branch": true, "whatsapp": true}`)
    seeded per plan (SUB-01) — a feature is granted when its key is truthy.
    """

    async def _dependency(
        current_user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> User:
        result = await session.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
        tenant = result.scalar_one_or_none()

        plan = None
        if tenant is not None and tenant.plan_id is not None:
            plan_result = await session.execute(
                select(SubscriptionPlan).where(SubscriptionPlan.id == tenant.plan_id)
            )
            plan = plan_result.scalar_one_or_none()

        granted = bool(plan.feature_limits.get(feature)) if plan else False
        if not granted:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": {
                        "code": "PLAN_UPGRADE_REQUIRED",
                        "message": "This feature requires a higher plan.",
                        "feature": feature,
                        "current_plan": plan.name if plan else None,
                        "upgrade_url": "/#pricing",
                    }
                },
            )
        return current_user

    return _dependency
