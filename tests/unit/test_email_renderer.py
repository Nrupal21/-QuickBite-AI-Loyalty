"""Unit tests for app.services.email_renderer — Jinja rendering, escaping, DB copy.

The parametrised render-every-template test is the mandatory counterweight to
StrictUndefined: without it, a renamed context key becomes a silent production
non-delivery discovered by a customer rather than by CI.

The DB-fallback tests all assert the same property from different angles: no
condition of the notification_templates table — missing row, unapproved row,
broken copy, database down — may stop a transactional email from going out.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models.static_data import NotificationTemplate
from app.services import email_renderer


@pytest.fixture(autouse=True)
def clear_template_cache():
    """The copy cache is module-level, so it would leak across tests."""
    email_renderer.invalidate_template_cache()
    yield
    email_renderer.invalidate_template_cache()


@pytest.fixture
def db_rows(mocker):
    """Patch the session factory so the renderer sees a controlled row set."""

    def _apply(rows: list[NotificationTemplate]):
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        factory = mocker.patch(
            "app.services.email_renderer.async_session_factory", return_value=ctx
        )
        return factory, session

    return _apply


def make_row(name: str, body: str, subject: str | None = None, approved: bool = True):
    return NotificationTemplate(
        name=name, channel="email", subject=subject, body=body, is_approved=approved
    )

# Every template, with a context representative of its real caller. Adding a
# template without adding a row here leaves it untested by construction.
TEMPLATE_CONTEXTS: dict[str, dict] = {
    "verification_email": {"verification_url": "https://marcos.quickbite.ai/verify?token=abc"},
    "account_locked_email": {"unlock_at": "14:30 UTC"},
    "staff_invite_email": {
        "accept_url": "https://marcos.quickbite.ai/team/accept-invite?token=xyz",
        "role_label": "Manager",
    },
    "otp_code_email": {"otp": "482913"},
}


@pytest.mark.parametrize("name", sorted(TEMPLATE_CONTEXTS))
@pytest.mark.asyncio
async def test_every_template_renders_both_parts(name):
    rendered = await email_renderer.render(name, TEMPLATE_CONTEXTS[name])

    assert rendered.subject
    assert rendered.html.strip()
    assert rendered.text.strip()
    # The layout wrapped it rather than the block rendering standalone.
    assert "QuickBite" in rendered.html
    assert "QuickBite" in rendered.text


@pytest.mark.asyncio
async def test_missing_context_key_raises_rather_than_shipping_a_blank():
    """StrictUndefined: a typo'd key must fail loudly, not render an empty string."""
    from jinja2 import UndefinedError

    with pytest.raises(UndefinedError):
        await email_renderer.render("otp_code_email", {})


@pytest.mark.asyncio
async def test_subjects_match_the_pre_smtp_wording():
    """Moving to templates must not silently change what lands in an inbox."""
    assert (await email_renderer.render(
        "verification_email", TEMPLATE_CONTEXTS["verification_email"]
    )).subject == "Verify your QuickBite account"
    assert (await email_renderer.render(
        "otp_code_email", TEMPLATE_CONTEXTS["otp_code_email"]
    )).subject == "Your QuickBite login code"


@pytest.mark.asyncio
async def test_otp_code_appears_in_both_parts():
    rendered = await email_renderer.render("otp_code_email", {"otp": "482913"})

    assert "482913" in rendered.html
    assert "482913" in rendered.text


@pytest.mark.asyncio
async def test_urls_are_spelled_out_in_the_plaintext_part():
    """A text-only client must never be left with 'click the button above'."""
    url = "https://marcos.quickbite.ai/verify?token=abc&src=email"

    rendered = await email_renderer.render("verification_email", {"verification_url": url})

    assert url in rendered.text


# --- escaping -----------------------------------------------------------


@pytest.mark.asyncio
async def test_html_part_escapes_injected_markup():
    rendered = await email_renderer.render("otp_code_email", {"otp": "<script>alert(1)</script>"})

    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


@pytest.mark.asyncio
async def test_text_part_is_not_escaped():
    """Escaping the plaintext alternative would render an ampersand as &amp;
    and an apostrophe as &#39; — visible garbage in a text client."""
    url = "https://marcos.quickbite.ai/verify?token=abc&next=/dashboard"

    rendered = await email_renderer.render("verification_email", {"verification_url": url})

    assert "&amp;" not in rendered.text
    assert "&next=" in rendered.text
    # The apostrophe in the shared footer stays literal too.
    assert "&#39;" not in rendered.text


# --- notification_templates resolution ----------------------------------


@pytest.mark.asyncio
async def test_approved_db_row_overrides_disk_copy(db_rows):
    db_rows([make_row("otp_code_email", "Custom body with {{ otp }}.", subject="Custom subject")])

    rendered = await email_renderer.render("otp_code_email", {"otp": "482913"})

    assert rendered.subject == "Custom subject"
    assert "Custom body with 482913." in rendered.text
    assert "Custom body with 482913." in rendered.html


@pytest.mark.asyncio
async def test_db_row_without_subject_keeps_the_default(db_rows):
    db_rows([make_row("otp_code_email", "Body {{ otp }}", subject=None)])

    rendered = await email_renderer.render("otp_code_email", {"otp": "482913"})

    assert rendered.subject == "Your QuickBite login code"


@pytest.mark.asyncio
async def test_unapproved_row_falls_back_to_disk(db_rows):
    db_rows([make_row("otp_code_email", "Unapproved draft {{ otp }}", approved=False)])

    rendered = await email_renderer.render("otp_code_email", {"otp": "482913"})

    assert "Unapproved draft" not in rendered.text
    assert "482913" in rendered.text  # the disk template still delivered the code


@pytest.mark.asyncio
async def test_missing_row_falls_back_to_disk(db_rows):
    db_rows([])

    rendered = await email_renderer.render("otp_code_email", {"otp": "482913"})

    assert "482913" in rendered.text
    assert rendered.subject == "Your QuickBite login code"


@pytest.mark.asyncio
async def test_database_outage_falls_back_to_disk(mocker):
    """An unreachable DB must not stop an OTP going out."""
    mocker.patch(
        "app.services.email_renderer.async_session_factory",
        side_effect=OSError("connection refused"),
    )

    rendered = await email_renderer.render("otp_code_email", {"otp": "482913"})

    assert "482913" in rendered.text


@pytest.mark.asyncio
async def test_broken_db_copy_falls_back_to_disk(db_rows):
    """Operator copy referencing an unknown key degrades instead of failing."""
    db_rows([make_row("otp_code_email", "Hello {{ nonexistent_key }}")])

    rendered = await email_renderer.render("otp_code_email", {"otp": "482913"})

    assert "482913" in rendered.text


@pytest.mark.asyncio
async def test_db_body_markup_is_escaped_not_rendered(db_rows):
    """Write access to notification_templates must not become stored XSS in
    customer mail — the body is plain text, never HTML, and never |safe."""
    db_rows([make_row("otp_code_email", "<script>alert(1)</script> code {{ otp }}")])

    rendered = await email_renderer.render("otp_code_email", {"otp": "482913"})

    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


@pytest.mark.asyncio
async def test_db_body_cannot_reach_python_internals(db_rows):
    """SandboxedEnvironment: a DB body is template source from outside the
    codebase, so SSTI is the sibling risk to stored XSS."""
    db_rows([make_row("otp_code_email", "{{ ''.__class__.__mro__ }}")])

    rendered = await email_renderer.render("otp_code_email", {"otp": "482913"})

    # Sandbox raised, so the disk fallback delivered the email instead.
    assert "__mro__" not in rendered.html
    assert "482913" in rendered.text


@pytest.mark.asyncio
async def test_db_body_paragraphs_split_on_blank_lines(db_rows):
    db_rows([make_row("otp_code_email", "First para.\n\nSecond para. {{ otp }}")])

    rendered = await email_renderer.render("otp_code_email", {"otp": "482913"})

    assert rendered.html.count("<p style=\"margin:0 0 16px 0;\">") == 2


@pytest.mark.asyncio
async def test_copy_is_cached_across_sends(db_rows):
    factory, _ = db_rows([make_row("otp_code_email", "Cached {{ otp }}")])

    await email_renderer.render("otp_code_email", {"otp": "111111"})
    await email_renderer.render("otp_code_email", {"otp": "222222"})

    factory.assert_called_once()  # one query served both sends


@pytest.mark.asyncio
async def test_invalidate_cache_forces_a_reload(db_rows):
    factory, _ = db_rows([make_row("otp_code_email", "Cached {{ otp }}")])

    await email_renderer.render("otp_code_email", {"otp": "111111"})
    email_renderer.invalidate_template_cache()
    await email_renderer.render("otp_code_email", {"otp": "222222"})

    assert factory.call_count == 2
