"""Seed static.notification_templates from notification_service.DEFAULTS. Idempotent.

Usage: python scripts/seed_templates.py [--overwrite]

The rows are Super Admin *overrides* — the service falls back to the built-in
DEFAULTS whenever a row is missing or unapproved, so seeding is optional for
correct delivery. It exists so an admin has something to edit in place rather
than starting from a blank row, and so WhatsApp copy is visible for the Meta
pre-approval workflow.

Existing rows are left alone by default: this seeder must never clobber copy an
admin has reworded. Pass --overwrite to reset every row back to the built-in
text (useful after changing DEFAULTS).

WhatsApp templates seed with is_approved=False. Meta must approve the copy
before it may be sent, and the service treats unapproved rows as absent, so an
un-approved WhatsApp row falls back to DEFAULTS rather than going out un-vetted.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.base import async_session_factory  # noqa: E402
from app.db.models.static_data import NotificationTemplate  # noqa: E402
from app.services.notification_service import CHANNEL_WHATSAPP, DEFAULTS  # noqa: E402


async def seed(overwrite: bool = False) -> None:
    async with async_session_factory() as session:
        for name, spec in sorted(DEFAULTS.items()):
            result = await session.execute(
                select(NotificationTemplate).where(NotificationTemplate.name == name)
            )
            existing = result.scalar_one_or_none()
            # Email/SMS copy is ours to approve; WhatsApp waits on Meta.
            approved = spec.channel != CHANNEL_WHATSAPP

            if existing is None:
                session.add(
                    NotificationTemplate(
                        name=name,
                        channel=spec.channel,
                        subject=spec.subject,
                        body=spec.body,
                        is_approved=approved,
                    )
                )
                print(f"created template {name} ({spec.channel})")
            elif overwrite:
                existing.channel = spec.channel
                existing.subject = spec.subject
                existing.body = spec.body
                existing.is_approved = approved
                print(f"overwrote template {name}")
            else:
                print(f"template {name} already exists — left as is")
        await session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="reset existing rows to the built-in copy (discards admin edits)",
    )
    args = parser.parse_args()
    asyncio.run(seed(overwrite=args.overwrite))
