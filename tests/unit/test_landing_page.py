"""Smoke tests for the landing page (STITCH-12) and static file serving."""

import pytest


@pytest.mark.asyncio
async def test_landing_page_renders(client):
    response = await client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="hero-canvas"' in html  # Three.js mount point (STITCH-12 criterion)
    assert "Turn Every Meal Into a 5-Star" in html
    assert "bg-[#FF6B35]" in html  # orange Start Free CTA
    assert "scroll-trigger-section" in html  # GSAP reveal hooks
    assert 'id="pricing"' in html
    assert 'id="reviews"' in html  # horizontal scroll gallery
    assert "card-showcase" in html  # loyalty card / QR / stamp section
    assert 'id="loyalty-card-3d"' in html
    assert "stamp-mark" in html
    assert "#D4A537" in html  # Foil Gold brand-exception token
    assert "#0B0F14" in html  # Obsidian brand-exception token


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
