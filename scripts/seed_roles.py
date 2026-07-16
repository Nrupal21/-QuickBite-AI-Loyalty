"""Seed the 4 static roles (Doc 2 Table 3). Idempotent — safe to re-run.

Usage: python scripts/seed_roles.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.base import async_session_factory  # noqa: E402
from app.db.models.user import Role  # noqa: E402

ROLES = [
    {
        "name": "SUPER_ADMIN",
        "level": 1,
        "mfa_required": True,
        "permissions": {"all": True},
    },
    {
        "name": "OWNER",
        "level": 2,
        "mfa_required": True,
        "permissions": {
            "manage_team": True,
            "manage_loyalty": True,
            "approve_responses": True,
            "manage_billing": True,
        },
    },
    {
        "name": "MANAGER",
        "level": 3,
        "mfa_required": True,
        "permissions": {"manage_loyalty": True, "approve_responses": True},
    },
    {
        "name": "STAFF",
        "level": 4,
        "mfa_required": False,
        "permissions": {"view_dashboard": True},
    },
]


async def seed() -> None:
    async with async_session_factory() as session:
        for spec in ROLES:
            existing = await session.execute(select(Role).where(Role.name == spec["name"]))
            if existing.scalar_one_or_none() is None:
                session.add(Role(**spec))
                print(f"created role {spec['name']}")
            else:
                print(f"role {spec['name']} already exists")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
