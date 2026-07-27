"""QuickBite — Twilio SMS/WhatsApp + SMTP email.

AUTH-01 implements the verification email. AUTH-02 adds the account-locked
alert. AUTH-04 adds the staff invite. NEW-OTP-01 adds customer OTP delivery via
Twilio SMS or email. WhatsApp reward alerts (NICE-02) arrive with their ticket.
Always mocked in tests — never call the real APIs from the suite.

These four email functions are deliberately thin façades over
`core/email_transport` (how to send) and `services/email_renderer` (what to
say). They keep their original names, signatures, return types, and structlog
event prefixes because every existing test patches them by attribute on this
module — e.g. "app.services.auth_service.messaging_service.send_verification_email".

Delivery stays best-effort: a False return never fails the calling flow. The
callers record the boolean in their audit trail so a delivery outage is
visible without taking the product down with it.
"""

import asyncio
from datetime import datetime

import structlog
from twilio.rest import Client as TwilioClient

from app.core import email_transport
from app.core.config import settings
from app.services import email_renderer

logger = structlog.get_logger(__name__)


async def send_verification_email(to_email: str, verification_url: str) -> bool:
    """Send the account-verification link. Returns False when skipped/failed.

    Registration must not fail if email delivery is down — the caller logs the
    outcome and the user can re-register to trigger a fresh token.
    """
    rendered = await email_renderer.render(
        "verification_email", {"verification_url": verification_url}
    )
    return await email_transport.send_email(
        to_email=to_email,
        subject=rendered.subject,
        html_body=rendered.html,
        text_body=rendered.text,
        log_event="email.verification",
    )


async def send_account_locked_email(to_email: str, unlock_at: datetime) -> bool:
    """Alert the owner their account was locked after repeated failed logins.

    Best-effort: login lockout must not fail if email delivery is down.
    """
    # Formatted here rather than in the template: how a timestamp is presented
    # is the sender's decision, and passing a raw datetime into a Jinja context
    # invites locale drift between the HTML and text parts.
    rendered = await email_renderer.render(
        "account_locked_email", {"unlock_at": unlock_at.strftime("%H:%M UTC")}
    )
    return await email_transport.send_email(
        to_email=to_email,
        subject=rendered.subject,
        html_body=rendered.html,
        text_body=rendered.text,
        log_event="email.account_locked",
    )


async def send_staff_invite_email(to_email: str, accept_url: str, role_name: str) -> bool:
    """Invite a Manager or Staff member to join a restaurant's team (AUTH-04).

    Best-effort like the other senders — but note the invite token is already
    in Redis by the time this runs, so a delivery failure leaves a valid,
    unreachable invite. The Owner's remedy is to re-invite, which mints a
    fresh token.
    """
    rendered = await email_renderer.render(
        "staff_invite_email",
        {"accept_url": accept_url, "role_label": role_name.replace("_", " ").title()},
    )
    sent = await email_transport.send_email(
        to_email=to_email,
        subject=rendered.subject,
        html_body=rendered.html,
        text_body=rendered.text,
        log_event="email.staff_invite",
    )
    if sent:
        logger.info("email.staff_invite.role", role=role_name)
    return sent


async def send_otp_email(to_email: str, otp: str) -> bool:
    """Deliver a customer login OTP via email. Best-effort."""
    rendered = await email_renderer.render("otp_code_email", {"otp": otp})
    return await email_transport.send_email(
        to_email=to_email,
        subject=rendered.subject,
        html_body=rendered.html,
        text_body=rendered.text,
        log_event="email.otp",
    )


async def send_otp_sms(to_phone: str, otp: str) -> bool:
    """Deliver a customer login OTP via Twilio SMS. Best-effort, like the email senders."""
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning("sms.otp.skipped_no_credentials")
        return False

    body = settings.CUSTOMER_OTP_SMS_TEMPLATE.format(otp=otp)
    try:
        # The Twilio client is synchronous. Called inline it would block the
        # event loop for a full HTTPS round trip on every OTP request — the
        # same bug the email path had before it moved to aiosmtplib.
        await asyncio.to_thread(
            TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN).messages.create,
            body=body,
            from_=settings.TWILIO_FROM_NUMBER,
            to=to_phone,
        )
    except Exception as exc:
        logger.error("sms.otp.send_failed", error=str(exc))
        return False
    logger.info("sms.otp.sent")
    return True
