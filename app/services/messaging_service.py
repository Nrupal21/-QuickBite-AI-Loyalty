"""QuickBite — Delivery façades: email via SMTP, SMS/WhatsApp via Twilio.

This module owns *routing*, not wording and not wire protocol:

  copy     -> services/email_renderer.py (email) or notification_service (SMS)
  wire     -> core/email_transport.py (SMTP/SendGrid) or the Twilio client
  routing  -> here

Every sender is best-effort and returns a bool: a message that fails to go out
must never fail the request that triggered it. Registration, login lockout, and
loyalty scans all keep working with the mail server down; the caller logs the
outcome into its audit trail. Always mocked in tests — never call the real APIs
from the suite.

Email and SMS resolve copy from different layers, which is deliberate rather
than accidental duplication: email needs a matched HTML + plaintext pair
wrapped in an on-disk layout (email_renderer), while SMS and WhatsApp are a
single unstyled string where `notification_service`'s `{name}` formatter is
the right tool. `notify()` below therefore serves the non-email channels only.

Both Twilio calls are synchronous SDK calls pushed to a worker thread. Calling
them inline would stall the event loop for a full HTTPS round trip on every
OTP request — the same bug `email_transport` exists to fix on the mail side.
"""

import asyncio
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.rest import Client as TwilioClient

from app.core import email_transport
from app.core.config import settings
from app.services import email_renderer, notification_service
from app.services.notification_service import (
    CHANNEL_EMAIL,
    CHANNEL_SMS,
    CHANNEL_WHATSAPP,
    RenderedMessage,
)

logger = structlog.get_logger(__name__)


# --- Email --------------------------------------------------------------


async def send_rendered_email(
    template_name: str, to_email: str, *, log_event: str, **context
) -> bool:
    """Render `template_name` and hand both MIME parts to the transport.

    `log_event` is the structlog prefix the operator greps for
    (`email.otp.sent`, `email.otp.send_failed`); it is a label only and must
    never carry PII or an OTP.
    """
    rendered = await email_renderer.render(template_name, context)
    return await email_transport.send_email(
        to_email=to_email,
        subject=rendered.subject,
        html_body=rendered.html,
        text_body=rendered.text,
        log_event=log_event,
    )


# --- SMS / WhatsApp -----------------------------------------------------


# The structlog bound-logger signature is `log(event, **kw)`, so `event` is
# reserved: passing `event=` as a keyword raises "got multiple values for
# argument 'event'" and takes down the very failure path that was trying to
# report a problem. The template name is bound as `template` instead.


async def send_sms(to_phone: str, body: str, *, template: str) -> bool:
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning("sms.skipped_no_credentials", template=template)
        return False

    try:
        client = TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        await asyncio.to_thread(
            client.messages.create,
            body=body,
            from_=settings.TWILIO_FROM_NUMBER,
            to=to_phone,
        )
    except Exception as exc:
        logger.error("sms.send_failed", template=template, error=str(exc))
        return False
    logger.info("sms.sent", template=template)
    return True


async def send_whatsapp(to_phone: str, body: str, *, template: str) -> bool:
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_WHATSAPP_FROM:
        logger.warning("whatsapp.skipped_no_credentials", template=template)
        return False

    try:
        client = TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        await asyncio.to_thread(
            client.messages.create,
            body=body,
            from_=f"whatsapp:{settings.TWILIO_WHATSAPP_FROM}",
            to=f"whatsapp:{to_phone}",
        )
    except Exception as exc:
        logger.error("whatsapp.send_failed", template=template, error=str(exc))
        return False
    logger.info("whatsapp.sent", template=template)
    return True


async def _dispatch(rendered: RenderedMessage, to: str, *, template: str) -> bool:
    if rendered.channel == CHANNEL_SMS:
        return await send_sms(to, rendered.body, template=template)
    if rendered.channel == CHANNEL_WHATSAPP:
        return await send_whatsapp(to, rendered.body, template=template)
    if rendered.channel == CHANNEL_EMAIL:
        # notification_service bodies are a single HTML fragment with no
        # plaintext twin and no layout, so sending one would ship unstyled,
        # HTML-only mail — a strong spam signal and unreadable in text
        # clients. Email copy belongs in app/templates/emails/ instead.
        # TODO(EMAIL-02): the security_* and loyalty_* email templates in
        # notification_service.DEFAULTS still have no disk template, so they
        # cannot be sent until one is written for each.
        logger.error(
            "notification.email_channel_unsupported",
            template=template,
            hint="add a template to app/templates/emails/ and use send_rendered_email()",
        )
        return False
    logger.error("notification.unknown_channel", template=template, channel=rendered.channel)
    return False


async def notify(
    template_name: str, to: str, session: AsyncSession | None = None, **context
) -> bool:
    """Render an SMS/WhatsApp template and deliver it on the channel it declares.

    `session` is optional: pass it from a request handler so a Super Admin's
    template override applies, or omit it in a Celery task that has already
    closed its session and the built-in copy is used.
    """
    rendered = (
        await notification_service.render(session, template_name, **context)
        if session is not None
        else notification_service.render_default(template_name, **context)
    )
    return await _dispatch(rendered, to, template=template_name)


# --- Named senders ------------------------------------------------------
# Thin wrappers so call sites read as intent ("send the invite") rather than as
# a template lookup, and so the context each template needs is stated in one
# place instead of at every caller.
#
# The two senders with a call to action pass `cta_url`/`cta_label` on top of
# their own URL key. The on-disk templates use the specific key, but operator
# copy in static.notification_templates is escaped plain text that cannot
# contain a link — `_db_body.html` renders the button from cta_url alone. Omit
# it and a Super Admin rewording the verification email silently ships one with
# no way to verify.


async def send_verification_email(to_email: str, verification_url: str) -> bool:
    return await send_rendered_email(
        "verification_email",
        to_email,
        log_event="email.verification",
        verification_url=verification_url,
        cta_url=verification_url,
        cta_label="Verify my account",
    )


async def send_account_locked_email(to_email: str, unlock_at: datetime) -> bool:
    return await send_rendered_email(
        "account_locked_email",
        to_email,
        log_event="email.account_locked",
        unlock_at=unlock_at.strftime("%H:%M UTC"),
    )


async def send_staff_invite_email(to_email: str, accept_url: str, role_name: str) -> bool:
    return await send_rendered_email(
        "staff_invite_email",
        to_email,
        log_event="email.staff_invite",
        accept_url=accept_url,
        # "SUPER_ADMIN" is a wire value, not something to show a human.
        role_label=role_name.replace("_", " ").title(),
        cta_url=accept_url,
        cta_label="Set up my account",
    )


async def send_otp_email(to_email: str, otp: str) -> bool:
    return await send_rendered_email("otp_code_email", to_email, log_event="email.otp", otp=otp)


async def send_otp_sms(to_phone: str, otp: str, session: AsyncSession | None = None) -> bool:
    return await notify("customer_otp_sms", to_phone, session, otp=otp)
