"""Seed the business categories and the plans each one may choose from.

Idempotent — safe to re-run. Run it AFTER scripts/seed_plans.py, since the
plan→category mapping resolves plans by `name`.

Why the mapping is not "every plan for everyone": a food truck has one
location and no team, so Enterprise (unlimited branches, white-label) is
noise on its pricing page; a multi-outlet QSR chain starting on Starter's
1-branch limit is a support ticket waiting to happen. `plans` per category
below is the list "Join Us" renders after the category is picked. A category
mapped to nothing at all falls back to every active plan (see
BillingService.list_active_plans) rather than showing an empty page.

Connects as the *owner* role (`alembic_database_url`), not the app role.
`static.*` grants `quickbite_app` SELECT only — reference data is written by
migrations and Super Admin, never by the running application — so seeding
through app/db/base.py's session factory fails with "permission denied for
table business_categories". That grant is the design, not an oversight; the
seeder is the thing that has to reach for the right credential.

Usage: python scripts/seed_categories.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.models.static_data import BusinessCategory, PlanCategory  # noqa: E402
from app.db.models.subscription import SubscriptionPlan  # noqa: E402

CATEGORIES = [
    {
        "slug": "restaurant",
        "display_name": "Restaurant",
        "tagline": "Dine-in tables, a full menu, a regular crowd",
        "icon_key": "plate",
        "sort_order": 10,
        "plans": ["starter", "pro", "enterprise"],
    },
    {
        "slug": "cafe",
        "display_name": "Café",
        "tagline": "Coffee, counter service, people who come back weekly",
        "icon_key": "cup",
        "sort_order": 20,
        "plans": ["starter", "pro"],
    },
    {
        "slug": "cloud-kitchen",
        "display_name": "Cloud Kitchen",
        "tagline": "Delivery only — reviews are the whole storefront",
        "icon_key": "moped",
        "sort_order": 30,
        "plans": ["starter", "pro", "enterprise"],
    },
    {
        "slug": "qsr",
        "display_name": "Quick Service",
        "tagline": "High volume, fast counter, several outlets",
        "icon_key": "bag",
        "sort_order": 40,
        "plans": ["pro", "enterprise"],
    },
    {
        "slug": "bakery",
        "display_name": "Bakery & Sweets",
        "tagline": "Daily regulars and festival rushes",
        "icon_key": "croissant",
        "sort_order": 50,
        "plans": ["starter", "pro"],
    },
    {
        "slug": "bar",
        "display_name": "Bar & Pub",
        "tagline": "Evening trade, events, a loyal weekend list",
        "icon_key": "glass",
        "sort_order": 60,
        "plans": ["pro", "enterprise"],
    },
    {
        "slug": "fine-dining",
        "display_name": "Fine Dining",
        "tagline": "Reputation is the reservation book",
        "icon_key": "cloche",
        "sort_order": 70,
        "plans": ["pro", "enterprise"],
    },
    {
        "slug": "food-truck",
        "display_name": "Food Truck",
        "tagline": "One counter, moving location, word of mouth",
        "icon_key": "truck",
        "sort_order": 80,
        # Deliberately no Enterprise: unlimited branches and white-label are
        # meaningless to a single mobile counter.
        "plans": ["starter", "pro"],
    },
]


async def seed() -> None:
    engine = create_async_engine(settings.alembic_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        plans_by_name: dict[str, SubscriptionPlan] = {
            plan.name: plan
            for plan in (await session.execute(select(SubscriptionPlan))).scalars()
        }
        if not plans_by_name:
            print("no subscription plans found — run scripts/seed_plans.py first")
            return

        for spec in CATEGORIES:
            plan_names = spec.pop("plans")
            existing = await session.execute(
                select(BusinessCategory).where(BusinessCategory.slug == spec["slug"])
            )
            category = existing.scalar_one_or_none()
            if category is None:
                category = BusinessCategory(**spec)
                session.add(category)
                await session.flush()
                print(f"created category {spec['slug']}")
            else:
                print(f"category {spec['slug']} already exists")

            for order, plan_name in enumerate(plan_names):
                plan = plans_by_name.get(plan_name)
                if plan is None:
                    print(f"  ! plan {plan_name} missing — skipped")
                    continue
                mapped = await session.execute(
                    select(PlanCategory).where(
                        PlanCategory.plan_id == plan.id,
                        PlanCategory.category_id == category.id,
                    )
                )
                if mapped.scalar_one_or_none() is None:
                    session.add(
                        PlanCategory(
                            plan_id=plan.id, category_id=category.id, sort_order=order
                        )
                    )
                    print(f"  mapped {plan_name} -> {spec['slug']}")

        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
