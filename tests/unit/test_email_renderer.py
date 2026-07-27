"""Unit tests for app.services.email_renderer — Jinja rendering + escaping.

The parametrised render-every-template test is the mandatory counterweight to
StrictUndefined: without it, a renamed context key becomes a silent production
non-delivery discovered by a customer rather than by CI.
"""

import pytest

from app.services import email_renderer

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
