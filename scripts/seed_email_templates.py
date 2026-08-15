"""Seed the transactional email copy (Doc 2 §297). Idempotent — safe to re-run.

Bodies are PLAIN TEXT, not HTML: email_renderer escapes them and wraps them in
the on-disk layout. Typing markup here would be escaped and shown literally,
and that is deliberate — it is what stops DB write access from becoming a
stored-XSS vector into customer mail. Links belong in the `cta_url` /
`cta_label` context the layout renders.

`{{ }}` placeholders must match the context keys each messaging_service facade
passes, or the render falls back to the on-disk template and logs
`email.template.db_render_failed`.

Existing rows are SKIPPED, never updated — a Super Admin may have edited the
copy, and re-running this script must not silently revert their work.

Usage: python scripts/seed_email_templates.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.base import async_session_factory  # noqa: E402
from app.db.models.static_data import NotificationTemplate  # noqa: E402

TEMPLATES = [
    {
        "name": "verification_email",
        "channel": "email",
        "subject": "Verify your QuickBite account",
        "body": (
            "Confirm your email\n\n"
            "Welcome to QuickBite. Confirm this address to activate your restaurant "
            "and start collecting reviews and loyalty stamps.\n\n"
            "This link expires in 24 hours."
        ),
        "is_approved": True,
    },
    {
        "name": "account_locked_email",
        "channel": "email",
        "subject": "QuickBite — suspicious activity on your account",
        "body": (
            "We locked your account\n\n"
            "Someone made several failed login attempts on your QuickBite account, "
            "so we locked it as a precaution.\n\n"
            "You can try again after {{ unlock_at }}.\n\n"
            "If this wasn't you, change your password once the account unlocks. "
            "Nobody gained access — the lock happened before any successful login."
        ),
        "is_approved": True,
    },
    {
        "name": "staff_invite_email",
        "channel": "email",
        "subject": "You've been invited to a QuickBite team",
        "body": (
            "You've been invited\n\n"
            "You've been invited to join a restaurant on QuickBite as {{ role_label }}. "
            "Set up your account to get started.\n\n"
            "This invite expires in 7 days."
        ),
        "is_approved": True,
    },
    {
        "name": "otp_code_email",
        "channel": "email",
        "subject": "Your QuickBite login code",
        "body": (
            "Your login code\n\n"
            "Enter this code to sign in to your loyalty account: {{ otp }}\n\n"
            "This code expires in 5 minutes and can only be used once. "
            "QuickBite will never ask you for it — if someone does, don't share it."
        ),
        "is_approved": True,
    },
]


async def seed() -> None:
    async with async_session_factory() as session:
        for spec in TEMPLATES:
            existing = await session.execute(
                select(NotificationTemplate).where(NotificationTemplate.name == spec["name"])
            )
            if existing.scalar_one_or_none() is None:
                session.add(NotificationTemplate(**spec))
                print(f"created template {spec['name']}")
            else:
                print(f"template {spec['name']} already exists")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
