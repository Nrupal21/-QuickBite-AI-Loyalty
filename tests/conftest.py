"""QuickBite — Shared test fixtures.

Provides: async test client, mock services, DB session rollback.
"""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    """Async test client for FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def block_smtp(request, mocker):
    """Safety net: no test may open a real SMTP socket.

    Individual tests still patch the sender functions explicitly — this only
    catches paths nobody thought to mock, where a real connection attempt would
    be a slow, flaky failure that leaks credentials into a traceback.

    Patches the network call alone, so tests that exercise the transport can
    re-patch it locally (the later patch wins). Opt out with
    `@pytest.mark.allow_smtp`, which is registered in pyproject.toml because
    addopts carries --strict-markers.
    """
    if "allow_smtp" in request.keywords:
        return
    mocker.patch("app.core.email_transport.aiosmtplib.send", new=AsyncMock())
