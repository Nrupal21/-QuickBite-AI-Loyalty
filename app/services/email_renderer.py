"""QuickBite — Email copy: resolves a template name into subject + HTML + text.

`Jinja2Templates` in api/v1/routers/pages.py is request-bound
(`TemplateResponse(request, ...)`) and unusable outside an HTTP handler, so
emails get their own standalone Environment here.

Two environments, not one, because HTML and plaintext need opposite escaping.
Escaping the `.txt` part would render an apostrophe as `&#39;` and a URL's `&`
as `&amp;` in the plaintext alternative — visible garbage in text clients. The
HTML environment escapes string templates too (`default_for_string=True`),
which matters once DB-sourced copy is rendered through `from_string()`.

`render()` is async even though it only touches disk today: the
notification_templates lookup lands here next, and making it async now avoids
changing all four sender signatures a second time.
"""

from dataclasses import dataclass
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

logger = structlog.get_logger(__name__)

# Resolved from __file__, never from the CWD-relative "app/templates" string
# that pages.py uses — a Celery worker may run from a different directory.
_EMAIL_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "emails"

_html_env = Environment(
    loader=FileSystemLoader(_EMAIL_TEMPLATE_DIR),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=True),
    # A typo'd context key would otherwise render as an empty string and ship a
    # broken email that nobody notices. This only pays off alongside the
    # render-every-template test in tests/unit/test_email_renderer.py.
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

_text_env = Environment(  # nosec B701 — see below; this env renders text/plain only
    loader=FileSystemLoader(_EMAIL_TEMPLATE_DIR),
    # Deliberately off, and not an XSS risk: nothing this environment produces
    # is ever interpreted as markup. It renders the text/plain MIME part, where
    # escaping would corrupt the output — a URL's `&` would reach the reader as
    # `&amp;` and an apostrophe as `&#39;`. Anything user-facing as HTML goes
    # through _html_env above, which escapes strings as well as files.
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

# Kept verbatim from the pre-SMTP inline senders so no subject line changes.
# A DB row's `subject` overrides these once notification_templates is wired.
_DEFAULT_SUBJECTS: dict[str, str] = {
    "verification_email": "Verify your QuickBite account",
    "account_locked_email": "QuickBite — suspicious activity on your account",
    "staff_invite_email": "You've been invited to a QuickBite team",
    "otp_code_email": "Your QuickBite login code",
}


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


def _render_from_disk(name: str, context: dict) -> tuple[str, str]:
    return (
        _html_env.get_template(f"{name}.html").render(**context),
        _text_env.get_template(f"{name}.txt").render(**context),
    )


async def render(name: str, context: dict) -> RenderedEmail:
    """Resolve `name` into a ready-to-send subject, HTML body, and text body.

    `name` is both the disk template stem and the notification_templates row
    name, so the two sources can never drift apart.
    """
    html, text = _render_from_disk(name, context)
    return RenderedEmail(subject=_DEFAULT_SUBJECTS[name], html=html, text=text)
