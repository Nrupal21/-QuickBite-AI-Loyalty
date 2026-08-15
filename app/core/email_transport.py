"""QuickBite — Email transport: MIME assembly + provider dispatch (SMTP/SendGrid).

Infrastructure, not business logic — which is why it sits in `core/` alongside
cache_service, encryption, and rate_limiter rather than in `services/`. The
copy that goes *into* an email is a product concern and lives in
`services/email_renderer.py`; this module only knows how to put bytes on a wire.

Delivery is best-effort by design: every failure path returns False rather than
raising, so a dead mail server can never fail a registration, a lockout, an
invite, or an OTP request. Callers log the boolean into their audit trail.

The senders that call this are all `async def`, so the send must genuinely not
block: `aiosmtplib` is awaited natively, and the legacy SendGrid client (which
is synchronous) is pushed to a worker thread. Before this module existed, every
send blocked the event loop for the duration of an HTTPS round trip with no
timeout at all.
"""

import asyncio
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import aiosmtplib  # module import, not `from aiosmtplib import send` — keeps a stable patch target
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Headroom over aiosmtplib's own timeout so its more specific error wins the
# race and produces a better log line than a bare TimeoutError.
_OUTER_TIMEOUT_MARGIN_SECONDS = 2


def _build_message(to_email: str, subject: str, html_body: str, text_body: str) -> EmailMessage:
    """Assemble a multipart/alternative message with the plaintext part first.

    Order matters: RFC 2046 says the *last* alternative is the richest, so
    clients pick HTML when they can and fall back to text when they cannot.
    HTML-only mail is a strong spam signal and is unreadable in text clients
    and screen readers, so both parts are mandatory.
    """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.EMAIL_FROM_NAME, settings.email_from_address))
    message["To"] = to_email
    message["Message-ID"] = make_msgid(domain="quickbite.ai")
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


async def _send_via_smtp(message: EmailMessage, log_event: str) -> bool:
    if not (settings.SMTP_HOST and settings.SMTP_USERNAME and settings.SMTP_PASSWORD):
        logger.warning(f"{log_event}.skipped_no_credentials")
        return False

    # aiosmtplib's `timeout` bounds individual socket operations, but DNS
    # resolution plus connect -> STARTTLS -> AUTH can collectively outlast it.
    # The outer bound is what stops a hung Gmail connection from holding an OTP
    # request open for the entire HTTP timeout.
    await asyncio.wait_for(
        aiosmtplib.send(
            message,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USERNAME,
            password=settings.SMTP_PASSWORD,
            timeout=settings.SMTP_TIMEOUT_SECONDS,
            **settings.smtp_tls_kwargs,
        ),
        timeout=settings.SMTP_TIMEOUT_SECONDS + _OUTER_TIMEOUT_MARGIN_SECONDS,
    )
    return True


async def _send_via_sendgrid(message: EmailMessage, log_event: str) -> bool:
    """Legacy provider, kept as the escape hatch for when Gmail's quota bites.

    Imported lazily so the dependency can be dropped without touching this
    module. The client is synchronous, so it runs in a worker thread — calling
    it inline is the event-loop bug this module exists to fix.
    """
    if not settings.SENDGRID_API_KEY:
        logger.warning(f"{log_event}.skipped_no_api_key")
        return False

    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail

    html_part = message.get_body(preferencelist=("html",))
    mail = Mail(
        from_email=settings.email_from_address,
        to_emails=message["To"],
        subject=message["Subject"],
        html_content=html_part.get_content() if html_part else "",
    )
    await asyncio.to_thread(SendGridAPIClient(settings.SENDGRID_API_KEY).send, mail)
    return True


async def send_email(
    *,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    log_event: str,
) -> bool:
    """Deliver one email. Returns False on any failure — never raises.

    `log_event` is the structlog prefix owned by the calling sender (e.g.
    "email.otp"), so the existing `.sent` / `.send_failed` event names survive
    the move to this module unchanged. Never pass PII in it.

    Every setting is read here rather than captured into module constants —
    that is what makes the transport patchable in tests, and it is precisely
    why the old hardcoded FROM_EMAIL constant was untestable.
    """
    if not settings.EMAIL_ENABLED:
        logger.info(f"{log_event}.skipped_disabled")
        return False

    try:
        message = _build_message(to_email, subject, html_body, text_body)
        if settings.EMAIL_PROVIDER == "sendgrid":
            sent = await _send_via_sendgrid(message, log_event)
        else:
            sent = await _send_via_smtp(message, log_event)
    except asyncio.TimeoutError:
        logger.error(f"{log_event}.timeout", timeout_seconds=settings.SMTP_TIMEOUT_SECONDS)
        return False
    except Exception as exc:
        logger.error(
            f"{log_event}.send_failed", error=str(exc), error_class=type(exc).__name__
        )
        return False

    if sent:
        logger.info(f"{log_event}.sent", provider=settings.EMAIL_PROVIDER)
    return sent
