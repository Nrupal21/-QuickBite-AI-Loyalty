"""Smoke tests for the landing page (STITCH-12) and static file serving.

These assert STITCH-12's acceptance criteria, not the page's wording. The
earlier version pinned the exact hero headline, which made a copywriting change
look like a broken build — and pinned two brand hex values that live in
`static/css/brand-tokens.css`, which is why a hidden `<div>` full of unused
classes had been added to the template purely to satisfy this file. A test that
makes you ship dead markup is testing the wrong thing.

Copy and exact class names are free to change; the structural contract is not.
"""

import re

import pytest


@pytest.mark.asyncio
async def test_landing_page_renders(client):
    response = await client.get("/")
    assert response.status_code == 200
    html = response.text

    # Three.js mount point — named exactly in the STITCH-12 criteria.
    assert 'id="hero-canvas"' in html
    # GSAP ScrollTrigger hooks.
    assert "scroll-trigger-section" in html
    # Orange Start Free CTA, also named exactly in the criteria.
    assert "bg-[#FF6B35]" in html


@pytest.mark.asyncio
async def test_hero_is_full_viewport(client):
    """"Hero section: min-h-screen". Accepts the dvh form too: `100dvh` is the
    same intent expressed better, since it accounts for mobile browser chrome
    that `vh` famously does not."""
    html = (await client.get("/")).text
    hero = re.search(r"<section[^>]*id=\"hero-section\"[^>]*>", html)

    assert hero, "no #hero-section on the page"
    assert "min-h-screen" in hero.group(0) or "min-h-[100dvh]" in hero.group(0)


@pytest.mark.asyncio
async def test_hero_headline_is_present_and_weighted(client):
    """"Headline: Inter 48px/800". The criterion is typography, not wording —
    the specific sentence lives in the Stitch prompt, not the acceptance list,
    so asserting it would freeze the marketing copy."""
    html = (await client.get("/")).text
    headline = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)

    assert headline, "no <h1> on the page"
    assert re.sub(r"<[^>]+>", "", headline.group(1)).strip(), "<h1> is empty"
    # 800 weight and the Dark Ink token (#1E2A3A, exposed as ink-950).
    assert "font-extrabold" in headline.group(0)
    assert "ink-950" in headline.group(0)


@pytest.mark.asyncio
async def test_key_sections_are_present(client):
    html = (await client.get("/")).text

    for anchor in ('id="pricing"', 'id="reviews"', 'id="loyalty-card-3d"'):
        assert anchor in html, f"missing {anchor}"
    for hook in ("card-showcase", "stamp-mark"):
        assert hook in html, f"missing {hook}"


@pytest.mark.asyncio
async def test_brand_exception_tokens_are_defined_in_css(client):
    """Foil Gold and Obsidian are CSS custom properties, so this checks the
    stylesheet that defines them rather than the HTML that consumes them.

    Asserting them in the page body is what produced the hidden token `<div>`:
    the markup existed for no reason other than to contain these strings.
    """
    response = await client.get("/static/css/brand-tokens.css")

    assert response.status_code == 200
    assert "#D4A537" in response.text  # Foil Gold
    assert "#0B0F14" in response.text  # Obsidian

    # And the page must actually load it, or defining them proves nothing.
    assert "/static/css/brand-tokens.css" in (await client.get("/")).text


@pytest.mark.asyncio
async def test_landing_page_has_no_em_dashes(client):
    """design-taste-frontend §9.G: zero em/en-dash characters, no exceptions."""
    response = await client.get("/")
    html = response.text
    assert "—" not in html  # em dash —
    assert "–" not in html  # en dash –


@pytest.mark.asyncio
async def test_health_endpoint_still_works(client):
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
