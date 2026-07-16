"""QuickBite — Twilio SMS/WhatsApp + SendGrid messaging.

AUTH-01 implements the SendGrid verification email. OTP SMS (NEW-OTP-01)
and WhatsApp reward alerts (NICE-02) arrive with their tickets.
Always mocked in tests — never call the real APIs from the suite.
"""

import structlog
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.core.config import settings

logger = structlog.get_logger(__name__)

FROM_EMAIL = "no-reply@quickbite.ai"


async def send_verification_email(to_email: str, verification_url: str) -> bool:
    """Send the account-verification link. Returns False when skipped/failed.

    Registration must not fail if email delivery is down — the caller logs
    the outcome and the user can re-register to trigger a fresh token.
    """
    if not settings.SENDGRID_API_KEY:
        logger.warning("email.verification.skipped_no_api_key")
        return False

    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=to_email,
        subject="Verify your QuickBite account",
        html_content=(
            "<p>Welcome to QuickBite! Confirm your email to activate your restaurant:</p>"
            f'<p><a href="{verification_url}">Verify my account</a></p>'
            "<p>This link expires in 24 hours.</p>"
        ),
    )
    try:
        SendGridAPIClient(settings.SENDGRID_API_KEY).send(message)
    except Exception as exc:
        logger.error("email.verification.send_failed", error=str(exc))
        return False
    logger.info("email.verification.sent")
    return True
