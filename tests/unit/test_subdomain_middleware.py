"""Unit tests for SubdomainTenantMiddleware — one per TENANT-01 acceptance criterion.

Redis and the tenant-registry DB lookup are mocked; the middleware is driven as
a bare ASGI callable so no app import (and no circular import) is involved.
"""

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.middleware import subdomain as sd
from app.middleware.subdomain import SubdomainTenantMiddleware

MARCO_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _base_domain(mocker):
    mocker.patch.object(settings, "BASE_DOMAIN", "quickbite.ai")


@pytest.fixture
def cache(mocker):
    return SimpleNamespace(
        get=mocker.patch("app.middleware.subdomain.cache_service.get", AsyncMock(return_value=None)),
        set=mocker.patch("app.middleware.subdomain.cache_service.set", AsyncMock()),
        delete=mocker.patch("app.middleware.subdomain.cache_service.delete", AsyncMock()),
    )


def mock_db(mocker, row):
    """Patch the session factory so the tenant lookup returns `row` (or None)."""
    session = MagicMock()
    result = MagicMock()
    result.first.return_value = row
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def factory():
        yield session

    mocker.patch("app.middleware.subdomain.async_session_factory", factory)
    return session


def marco(active: bool = True):
    return SimpleNamespace(id=MARCO_ID, name="Marco's", is_active=active)


class Downstream:
    def __init__(self):
        self.scope = None

    async def __call__(self, scope, receive, send):
        self.scope = scope
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})


async def call(host="marcos.quickbite.ai", path="/", type_="http", accept="text/html"):
    app = Downstream()
    sent: list[dict] = []
    headers = [(b"accept", accept.encode())]
    if host is not None:
        headers.append((b"host", host.encode()))

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": type_, "path": path, "headers": headers}
    await SubdomainTenantMiddleware(app)(scope, receive, send)
    return app, sent


def status_of(sent):
    return sent[0]["status"]


def body_of(sent):
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


# --- AC1: marcos.quickbite.ai -> request.state.tenant_id = Marco's UUID -----


async def test_known_subdomain_sets_tenant_id_on_request_state(mocker, cache):
    mock_db(mocker, marco())
    app, sent = await call()
    assert status_of(sent) == 200
    assert app.scope["state"]["tenant_id"] == MARCO_ID


async def test_host_header_port_and_case_are_ignored(mocker, cache):
    mock_db(mocker, marco())
    app, _ = await call(host="Marcos.QuickBite.ai:8000")
    assert app.scope["state"]["tenant_id"] == MARCO_ID


# --- AC2: unknown subdomain -> 404 with friendly page -----------------------


async def test_unknown_subdomain_returns_404_friendly_page(mocker, cache):
    mock_db(mocker, None)
    app, sent = await call(host="nope.quickbite.ai")
    assert status_of(sent) == 404
    assert app.scope is None  # request never reached the app
    assert b"Restaurant not found" in body_of(sent)
    assert sent[0]["headers"] and any(k == b"content-type" and b"text/html" in v for k, v in sent[0]["headers"])


async def test_unknown_subdomain_on_api_path_returns_json_error(mocker, cache):
    mock_db(mocker, None)
    _, sent = await call(host="nope.quickbite.ai", path="/api/v1/dashboard/stats")
    assert status_of(sent) == 404
    assert json.loads(body_of(sent))["error"]["code"] == "TENANT_NOT_FOUND"


async def test_unknown_subdomain_is_negatively_cached_briefly(mocker, cache):
    mock_db(mocker, None)
    await call(host="nope.quickbite.ai")
    key, value = cache.set.await_args.args
    assert key == "tenant:subdomain:nope"
    assert json.loads(value) is None
    assert cache.set.await_args.kwargs["ttl"] == sd.TENANT_NEGATIVE_CACHE_TTL_SECONDS


@pytest.mark.parametrize("host", ["a.b.quickbite.ai", "-bad.quickbite.ai", "bad_.quickbite.ai"])
async def test_malformed_subdomain_is_404_without_any_lookup(mocker, cache, host):
    session = mock_db(mocker, marco())
    _, sent = await call(host=host)
    assert status_of(sent) == 404
    session.execute.assert_not_awaited()
    cache.get.assert_not_awaited()


# --- AC3: suspended tenant -> 403 with contact-support message --------------


async def test_suspended_tenant_returns_403_contact_support(mocker, cache):
    mock_db(mocker, marco(active=False))
    app, sent = await call()
    assert status_of(sent) == 403
    assert app.scope is None
    assert b"contact QuickBite support" in body_of(sent)


async def test_suspended_tenant_api_error_code(mocker, cache):
    mock_db(mocker, marco(active=False))
    _, sent = await call(path="/api/v1/auth/login")
    assert json.loads(body_of(sent))["error"]["code"] == "TENANT_SUSPENDED"


# --- AC4: Redis-cached lookup, 1-hour TTL ------------------------------------


async def test_lookup_is_cached_for_one_hour(mocker, cache):
    mock_db(mocker, marco())
    await call()
    key, value = cache.set.await_args.args
    assert key == "tenant:subdomain:marcos"
    assert json.loads(value) == {"id": str(MARCO_ID), "name": "Marco's", "active": True}
    assert cache.set.await_args.kwargs["ttl"] == 3600


async def test_cache_hit_skips_the_database(mocker, cache):
    session = mock_db(mocker, None)
    cache.get.return_value = json.dumps({"id": str(MARCO_ID), "name": "Marco's", "active": True})
    app, sent = await call()
    assert status_of(sent) == 200
    assert app.scope["state"]["tenant_id"] == MARCO_ID
    session.execute.assert_not_awaited()


async def test_cached_suspension_is_enforced(mocker, cache):
    cache.get.return_value = json.dumps({"id": str(MARCO_ID), "name": "Marco's", "active": False})
    _, sent = await call()
    assert status_of(sent) == 403


async def test_redis_outage_falls_back_to_database(mocker, cache):
    cache.get.side_effect = ConnectionError("redis down")
    cache.set.side_effect = ConnectionError("redis down")
    mock_db(mocker, marco())
    app, sent = await call()
    assert status_of(sent) == 200
    assert app.scope["state"]["tenant_id"] == MARCO_ID


async def test_database_failure_returns_503_not_a_guess(mocker, cache):
    session = mock_db(mocker, None)
    session.execute.side_effect = RuntimeError("db down")
    app, sent = await call()
    assert status_of(sent) == 503
    assert app.scope is None


async def test_invalidate_deletes_key_and_swallows_redis_errors(cache):
    await sd.invalidate_tenant_cache("Marcos")
    cache.delete.assert_awaited_once_with("tenant:subdomain:marcos")
    cache.delete.side_effect = ConnectionError("redis down")
    await sd.invalidate_tenant_cache("marcos")  # must not raise


# --- AC5: WebSocket upgrades --------------------------------------------------


async def test_websocket_upgrade_on_known_tenant_passes_through(mocker, cache):
    mock_db(mocker, marco())
    app, sent = await call(type_="websocket", path="/api/v1/dashboard/ws")
    assert app.scope["state"]["tenant_id"] == MARCO_ID
    assert sent == []  # middleware sent nothing; the route owns the handshake


async def test_websocket_unknown_tenant_closed_4404(mocker, cache):
    mock_db(mocker, None)
    app, sent = await call(host="nope.quickbite.ai", type_="websocket")
    assert sent == [{"type": "websocket.close", "code": sd.WS_CLOSE_NOT_FOUND}]
    assert app.scope is None


async def test_websocket_suspended_tenant_closed_4403(mocker, cache):
    mock_db(mocker, marco(active=False))
    _, sent = await call(type_="websocket")
    assert sent == [{"type": "websocket.close", "code": sd.WS_CLOSE_SUSPENDED}]


# --- Non-tenant hosts are untouched ------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["quickbite.ai", "www.quickbite.ai", "api.quickbite.ai", "localhost:8000", "127.0.0.1",
     "[::1]:8000", "evil.example.com", "marcos.quickbite.ai.evil.com", None],
)
async def test_non_tenant_hosts_pass_through_with_no_tenant(mocker, cache, host):
    session = mock_db(mocker, marco())
    app, sent = await call(host=host)
    assert status_of(sent) == 200
    assert app.scope["state"]["tenant_id"] is None
    session.execute.assert_not_awaited()


async def test_lifespan_scope_is_ignored(cache):
    app = Downstream()
    await SubdomainTenantMiddleware(app)({"type": "lifespan"}, None, None)
    assert app.scope == {"type": "lifespan"}
