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


@pytest.mark.asyncio
async def test_health_endpoint_still_works(client):
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
