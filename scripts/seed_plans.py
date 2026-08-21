"""Seed the 3 subscription plans (Doc 2 Table 6). Idempotent — safe to re-run.

feature_limits follow the Doc 1 plan matrix. Prices in INR paise.

Usage: python scripts/seed_plans.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.base import async_session_factory  # noqa: E402
from app.db.models.subscription import SubscriptionPlan  # noqa: E402

PLANS = [
    {
        "name": "starter",
        "display_name": "Starter",
        "price_monthly_inr": 0,
        "provider_plan_id": None,
        "trial_days": 0,
        "feature_limits": {
            "branches": 1,
            "ai_responses_pm": 100,
            "team_members": 2,
            "sms_pm": 50,
            "scratch_cards": False,
            "white_label": False,
            "ai_review_replies": False,
        },
    },
    {
        "name": "pro",
        "display_name": "Pro",
        "price_monthly_inr": 299900,
        "provider_plan_id": None,  # set from the Razorpay dashboard in SUB-01
        "trial_days": 14,
        "feature_limits": {
            "branches": 3,
            "ai_responses_pm": 1000,
            "team_members": 5,
            "sms_pm": 200,
            "scratch_cards": True,
            "white_label": False,
            "ai_review_replies": True,
        },
    },
    {
        "name": "enterprise",
        "display_name": "Enterprise",
        "price_monthly_inr": 999900,
        "provider_plan_id": None,
        "trial_days": -1,
        "feature_limits": {
            "branches": -1,
            "ai_responses_pm": -1,
            "team_members": -1,
            "sms_pm": -1,
            "scratch_cards": True,
            "white_label": True,
            "ai_review_replies": True,
        },
    },
]


async def seed() -> None:
    async with async_session_factory() as session:
        for spec in PLANS:
            existing = await session.execute(
                select(SubscriptionPlan).where(SubscriptionPlan.name == spec["name"])
            )
            if existing.scalar_one_or_none() is None:
                session.add(SubscriptionPlan(**spec))
                print(f"created plan {spec['name']}")
            else:
                print(f"plan {spec['name']} already exists")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
