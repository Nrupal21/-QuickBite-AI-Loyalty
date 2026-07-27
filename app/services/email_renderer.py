"""QuickBite — Email copy: resolves a template name into subject + HTML + text.

`Jinja2Templates` in api/v1/routers/pages.py is request-bound
(`TemplateResponse(request, ...)`) and unusable outside an HTTP handler, so
emails get their own standalone Environment here.

Two environments, not one, because HTML and plaintext need opposite escaping.
Escaping the `.txt` part would render an apostrophe as `&#39;` and a URL's `&`
as `&amp;` in the plaintext alternative — visible garbage in text clients.

## Where copy lives (two sources, one rule)

| Concern                                  | Owner                          | Changed by        |
|------------------------------------------|--------------------------------|-------------------|
| Copy — subject line and body prose        | `static.notification_templates` | Super Admin, no deploy |
| Presentation — layout, palette, buttons   | disk Jinja templates            | engineers, via PR |
| Fallback copy                             | disk templates + `_DEFAULT_SUBJECTS` | engineers, via PR |

An approved DB row overrides the on-disk *words*; the disk always owns the
*shell*. Doc 2 §297 requires copy to be editable without a deploy, and the
`NotificationTemplate` docstring says it must never be hardcoded — this
satisfies both while keeping a versioned fallback.

**Any** failure to resolve a DB row — missing, unapproved, or a database
outage — falls back to disk rather than raising. Making a transactional OTP or
verification email depend on an approval flag or on the database being
reachable is how password resets stop working on a Saturday.

DB bodies are treated as untrusted plain text, never HTML — see `_render_db_copy`.
"""

import time
from dataclasses import dataclass
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select

from app.core.config import settings
from app.db.base import async_session_factory
from app.db.models.static_data import NotificationTemplate

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

# Sandboxed, because a DB body is template *source* from outside the codebase.
# A plain Environment would evaluate `{{ ''.__class__.__mro__ }}` and turn write
# access to static.notification_templates into server-side template injection.
# Output is escaped downstream, so autoescape stays off here.
_db_body_env = SandboxedEnvironment(  # nosec B701 — output is escaped by _db_body.html
    autoescape=False, undefined=StrictUndefined
)

# Kept verbatim from the pre-SMTP inline senders so no subject line changes.
# A DB row's `subject` overrides these.
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


@dataclass(frozen=True)
class _TemplateCopy:
    """Plain snapshot of a DB row.

    Deliberately not the ORM object: the session that loaded it is closed by
    the time the cache is read, and a detached instance would raise on
    attribute access the moment anything expires it.
    """

    subject: str | None
    body: str
    is_approved: bool


_cache: dict[str, _TemplateCopy] | None = None
_cache_loaded_at: float = 0.0


def invalidate_template_cache() -> None:
    """Drop the cached copy. For tests and a future admin 'republish' endpoint."""
    global _cache, _cache_loaded_at
    _cache = None
    _cache_loaded_at = 0.0


async def _load_template_cache() -> dict[str, _TemplateCopy]:
    """Load every email-channel row in one query.

    In-process rather than Redis: the data is static, tiny (under ten rows),
    and not tenant-scoped, so a network hop plus ORM serialisation on every
    send would cost more than it saves. Each worker holding its own copy is
    fine, and 5 minutes of staleness on marketing copy is acceptable where
    500ms of extra latency on an OTP is not.
    """
    global _cache, _cache_loaded_at
    async with async_session_factory() as session:
        result = await session.execute(
            select(NotificationTemplate).where(NotificationTemplate.channel == "email")
        )
        _cache = {
            row.name: _TemplateCopy(
                subject=row.subject, body=row.body, is_approved=row.is_approved
            )
            for row in result.scalars().all()
        }
    _cache_loaded_at = time.monotonic()
    return _cache


async def _get_db_copy(name: str) -> _TemplateCopy | None:
    """Resolve one row, or None to mean 'use the disk fallback'."""
    global _cache
    expired = time.monotonic() - _cache_loaded_at > settings.EMAIL_TEMPLATE_CACHE_TTL_SECONDS
    if _cache is None or expired:
        await _load_template_cache()

    copy = (_cache or {}).get(name)
    if copy is None:
        logger.warning("email.template.db_miss", template=name)
        return None
    if not copy.is_approved:
        logger.warning("email.template.unapproved", template=name)
        return None
    return copy


def _render_from_disk(name: str, context: dict) -> tuple[str, str]:
    return (
        _html_env.get_template(f"{name}.html").render(**context),
        _text_env.get_template(f"{name}.txt").render(**context),
    )


def _render_db_copy(copy: _TemplateCopy, context: dict) -> tuple[str, str]:
    """Render a DB body as untrusted plain text wrapped in the on-disk shell.

    The body is never treated as HTML and never marked `|safe`. It is
    substituted, split on blank lines, and handed to `_db_body.html` as a list
    of strings, where Jinja's file autoescaping escapes each one. Without that,
    anyone with write access to static.notification_templates would hold a
    stored-XSS and phishing primitive aimed at every customer — on a channel
    where the recipient trusts the sender.

    Links therefore come from context variables the layout renders (`cta_url`,
    `cta_label`), never from raw anchors typed into the DB body.
    """
    substituted = _db_body_env.from_string(copy.body).render(**context)
    paragraphs = [block.strip() for block in substituted.split("\n\n") if block.strip()]
    body_context = {
        **context,
        "paragraphs": paragraphs,
        "cta_url": context.get("cta_url"),
        "cta_label": context.get("cta_label"),
    }
    return (
        _html_env.get_template("_db_body.html").render(**body_context),
        _text_env.get_template("_db_body.txt").render(**body_context),
    )


async def render(name: str, context: dict) -> RenderedEmail:
    """Resolve `name` into a ready-to-send subject, HTML body, and text body.

    `name` is both the disk template stem and the notification_templates row
    name, so the two sources can never drift apart.
    """
    default_subject = _DEFAULT_SUBJECTS[name]

    try:
        copy = await _get_db_copy(name)
    except Exception as exc:
        # A database problem must never stop an OTP or verification email.
        logger.warning(
            "email.template.lookup_failed",
            template=name,
            error=str(exc),
            error_class=type(exc).__name__,
        )
        copy = None

    if copy is not None:
        try:
            html, text = _render_db_copy(copy, context)
            return RenderedEmail(
                subject=copy.subject or default_subject, html=html, text=text
            )
        except Exception as exc:
            # Bad operator-authored copy degrades to the versioned original
            # rather than taking the email down.
            logger.warning(
                "email.template.db_render_failed",
                template=name,
                error=str(exc),
                error_class=type(exc).__name__,
            )

    html, text = _render_from_disk(name, context)
    return RenderedEmail(subject=default_subject, html=html, text=text)
