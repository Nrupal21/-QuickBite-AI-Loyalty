"""QuickBite — Template-backed notification copy (Doc 2 Table `notification_templates`).

Doc 2 line 297 requires notification copy to be "referenced by Celery tasks —
never hardcoded in task files". This module is that reference layer: callers
ask for a template by name and hand over a context dict, and get back rendered
subject/body for the right channel. Transport (SendGrid, Twilio) stays in
messaging_service.

Two-tier resolution, deliberately:

1. `static.notification_templates` — Super Admin can reword any message
   without a deploy, and WhatsApp copy must be pre-approved by Meta before use
   (`is_approved`), so an unapproved row is treated as absent.
2. DEFAULTS below — the built-in fallback. Copy lives *here*, not in the task
   files, so the Doc 2 rule holds; the DB row is an override, not the only
   source. Without this tier an unseeded database would silently stop sending
   password-reset and account-locked mail, which are exactly the messages that
   must never go missing.

Templates are static reference data (Doc 2: "safe to cache aggressively"), so
lookups are Redis-cached; `invalidate()` clears one after an admin edit.
"""

import json
import string
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.db.models.static_data import NotificationTemplate

logger = structlog.get_logger(__name__)

TEMPLATE_CACHE_TTL_SECONDS = 3600

CHANNEL_EMAIL = "email"
CHANNEL_SMS = "sms"
CHANNEL_WHATSAPP = "whatsapp"


@dataclass(frozen=True)
class TemplateSpec:
    """A message's channel and its copy, before context substitution."""

    channel: str
    body: str
    subject: str | None = None  # email only


@dataclass(frozen=True)
class RenderedMessage:
    channel: str
    body: str
    subject: str | None = None


class _SafeFormatter(string.Formatter):
    """`{name}` substitution with attribute and index access refused.

    Plain `str.format` would let a template body reach through a context value
    into the object graph — `{customer.encrypted_email}` or `{user.__class__}`
    — turning "edit some copy" into "read arbitrary attributes". Template rows
    are Super Admin-written, but copy editing should not carry that blast
    radius, so only bare field names resolve.
    """

    def get_field(self, field_name: str, args, kwargs):
        if "." in field_name or "[" in field_name:
            msg = f"unsupported placeholder {field_name!r} — plain {{name}} only"
            raise ValueError(msg)
        return super().get_field(field_name, args, kwargs)


_formatter = _SafeFormatter()


# Built-in copy. Placeholders are plain `{name}` fields matching the context
# dict each caller passes. Keep names stable — a DB override row is matched by
# name, and renaming one silently orphans the admin's edit.
DEFAULTS: dict[str, TemplateSpec] = {
    # --- Owner/Staff account lifecycle ---
    "staff_verification_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="Verify your QuickBite account",
        body=(
            "<p>Welcome to QuickBite! Confirm your email to activate your restaurant:</p>"
            '<p><a href="{verification_url}">Verify my account</a></p>'
            "<p>This link expires in 24 hours.</p>"
        ),
    ),
    "staff_invite_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="You've been invited to a QuickBite team",
        body=(
            "<p>You've been invited to join a restaurant on QuickBite as {role_name}.</p>"
            '<p><a href="{accept_url}">Set up my account</a></p>'
            "<p>This invite expires in 7 days.</p>"
        ),
    ),
    "account_locked_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="QuickBite — suspicious activity on your account",
        body=(
            "<p>We locked your account after several failed login attempts.</p>"
            "<p>You can try again after {unlock_at}.</p>"
            "<p>If this wasn't you, consider changing your password once unlocked.</p>"
        ),
    ),
    # --- Email OTP ---
    "customer_otp_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="Your QuickBite login code",
        body="<p>Your QuickBite login code is: <strong>{otp}</strong></p><p>Expires in 5 minutes.</p>",
    ),
    "customer_otp_sms": TemplateSpec(
        channel=CHANNEL_SMS,
        body="Your QuickBite code: {otp}",
    ),
    "staff_password_reset_otp_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="Your QuickBite password reset code",
        body=(
            "<p>Use this code to reset your QuickBite password: <strong>{otp}</strong></p>"
            "<p>It expires in {expires_minutes} minutes and can be used once.</p>"
            "<p>If you didn't ask for this, ignore this email — your password is unchanged.</p>"
        ),
    ),
    "staff_login_otp_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="Your QuickBite sign-in code",
        body=(
            "<p>Your sign-in code is: <strong>{otp}</strong></p>"
            "<p>It expires in {expires_minutes} minutes and can be used once.</p>"
            "<p>If you didn't try to sign in, someone may know your email — no action is needed, "
            "but do not share this code.</p>"
        ),
    ),
    "customer_recovery_otp_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="Recover your QuickBite loyalty account",
        body=(
            "<p>Use this code to get back into your loyalty account: <strong>{otp}</strong></p>"
            "<p>It expires in 5 minutes.</p>"
        ),
    ),
    # --- Security events ---
    "security_new_device_login_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="New sign-in to your QuickBite account",
        body=(
            "<p>Your account was signed in to from a device we haven't seen before.</p>"
            "<p>When: {occurred_at}</p>"
            "<p>If this was you, nothing to do. If not, change your password and sign out "
            "all devices from your account settings.</p>"
        ),
    ),
    "security_password_changed_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="Your QuickBite password was changed",
        body=(
            "<p>Your password was changed on {occurred_at}, and every signed-in device was "
            "signed out.</p>"
            "<p>If this wasn't you, reset your password immediately — whoever did it has been "
            "signed out too.</p>"
        ),
    ),
    "security_mfa_enrolled_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="Two-factor authentication is on",
        body=(
            "<p>Two-factor authentication was turned on for your QuickBite account on "
            "{occurred_at}.</p>"
            "<p>If this wasn't you, contact your restaurant owner right away.</p>"
        ),
    ),
    "security_team_member_added_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="A new team member joined your restaurant",
        body=(
            "<p>{member_label} joined your QuickBite team as {role_name} on {occurred_at}.</p>"
            "<p>If you didn't expect this, remove them from your team settings.</p>"
        ),
    ),
    "security_team_member_removed_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="A team member was removed",
        body=(
            "<p>Your QuickBite access for this restaurant was removed on {occurred_at}.</p>"
            "<p>If you think this is a mistake, contact your restaurant owner.</p>"
        ),
    ),
    # --- Loyalty events ---
    "loyalty_stamp_collected_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="You collected a stamp at {branch_name}",
        body=(
            "<p>Nice one — that's {stamp_count} of {stamps_required} stamps at {branch_name}.</p>"
            "<p>{stamps_remaining} to go until your next reward.</p>"
        ),
    ),
    "loyalty_reward_unlocked_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="Your reward at {branch_name} is ready",
        body=(
            "<p>You've earned {reward_label} at {branch_name}.</p>"
            "<p>Show this code when you order: <strong>{redemption_code}</strong></p>"
            "<p>Valid until {expires_at}.</p>"
        ),
    ),
    "loyalty_reward_unlocked_whatsapp": TemplateSpec(
        channel=CHANNEL_WHATSAPP,
        body=(
            "Your reward at {branch_name} is ready. Show code {redemption_code} when you order. "
            "Valid until {expires_at}."
        ),
    ),
    "loyalty_reward_expiring_email": TemplateSpec(
        channel=CHANNEL_EMAIL,
        subject="Your {branch_name} reward expires soon",
        body=(
            "<p>Your reward {reward_label} at {branch_name} expires on {expires_at}.</p>"
            "<p>Show code <strong>{redemption_code}</strong> before then to claim it.</p>"
        ),
    ),
}


def _cache_key(name: str) -> str:
    return f"notification_template:{name}"


async def _load_override(session: AsyncSession, name: str) -> TemplateSpec | None:
    """Fetch the Super Admin's override row, or None to fall through to DEFAULTS."""
    cached = await cache_service.get(_cache_key(name))
    if cached is not None:
        payload = json.loads(cached)
        # A cached miss is stored as null so a template with no override row
        # does not hit the DB on every single send.
        return TemplateSpec(**payload) if payload else None

    result = await session.execute(
        select(NotificationTemplate).where(NotificationTemplate.name == name)
    )
    row = result.scalar_one_or_none()

    # WhatsApp copy must be pre-approved by Meta before it may be sent, and an
    # admin drafting a reword shouldn't go live mid-edit — unapproved reads as absent.
    spec = (
        TemplateSpec(channel=row.channel, subject=row.subject, body=row.body)
        if row is not None and row.is_approved
        else None
    )
    await cache_service.set(
        _cache_key(name),
        json.dumps({"channel": spec.channel, "subject": spec.subject, "body": spec.body})
        if spec
        else "null",
        ttl=TEMPLATE_CACHE_TTL_SECONDS,
    )
    return spec


def _render_spec(spec: TemplateSpec, context: dict) -> RenderedMessage:
    return RenderedMessage(
        channel=spec.channel,
        subject=_formatter.vformat(spec.subject, (), context) if spec.subject else None,
        body=_formatter.vformat(spec.body, (), context),
    )


async def render(session: AsyncSession, name: str, **context) -> RenderedMessage:
    """Render `name` with `context`, preferring the DB override over DEFAULTS.

    A broken override (bad placeholder, missing context key) falls back to the
    built-in copy rather than raising: an admin's typo must not be able to stop
    a password-reset or account-locked email from going out.
    """
    default = DEFAULTS.get(name)
    if default is None:
        msg = f"unknown notification template {name!r} — add it to DEFAULTS"
        raise KeyError(msg)

    override = await _load_override(session, name)
    if override is not None:
        try:
            return _render_spec(override, context)
        except (KeyError, IndexError, ValueError) as exc:
            logger.error(
                "notification.template.override_render_failed",
                template=name,
                error=str(exc),
            )

    return _render_spec(default, context)


def render_default(name: str, **context) -> RenderedMessage:
    """Render straight from DEFAULTS, no DB session needed.

    For callers with no session in hand — Celery tasks that already committed
    and closed theirs, and the sync transport paths in messaging_service.
    """
    default = DEFAULTS.get(name)
    if default is None:
        msg = f"unknown notification template {name!r} — add it to DEFAULTS"
        raise KeyError(msg)
    return _render_spec(default, context)


async def invalidate(name: str) -> None:
    """Drop the cached override so an admin's edit takes effect immediately."""
    await cache_service.delete(_cache_key(name))
