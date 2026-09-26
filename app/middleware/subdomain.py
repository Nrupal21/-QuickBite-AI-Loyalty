"""QuickBite — Subdomain tenant resolution middleware (TENANT-01).

Pure ASGI (not BaseHTTPMiddleware) so one class covers both HTTP requests and
WebSocket upgrades. Reads the Host header, resolves `<slug>.<BASE_DOMAIN>` to a
tenant and sets `request.state.tenant_id` (a `uuid.UUID`, or `None` on hosts that
are not a tenant subdomain — the apex, `www`, an IP, localhost).

Outcomes for a tenant subdomain:
  * known + active   -> `state.tenant_id` set, request continues
  * unknown          -> 404 (friendly page for browsers, JSON error for API/XHR;
                        WebSocket closed with 4404)
  * suspended        -> 403 contact-support message (WebSocket closed with 4403)

Lookups are Redis-cached for 1 hour. Unknown slugs are negatively cached for a
short window so a wildcard-subdomain flood cannot turn into a DB flood. Cache
failures fail open to the database; a database failure returns 503 rather than
guessing at a tenant.

The cache must be invalidated whenever a tenant is created or its `is_active`
flag flips — call `invalidate_tenant_cache(subdomain)`.

This middleware only *identifies* the tenant from the hostname. It does not
authenticate anyone and does not bind RLS context; those still come from the
caller's JWT (see app/core/rls.py).
"""

import html
import json
import re
import uuid

import structlog
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import cache_service
from app.core.config import settings
from app.db.base import async_session_factory
from app.db.models.tenant import Tenant

logger = structlog.get_logger(__name__)

TENANT_CACHE_TTL_SECONDS = 3600  # 1 hour — TENANT-01 AC
TENANT_NEGATIVE_CACHE_TTL_SECONDS = 30
WS_CLOSE_NOT_FOUND = 4404
WS_CLOSE_SUSPENDED = 4403

# Subdomains that are QuickBite's own surfaces, never a restaurant.
RESERVED_SUBDOMAINS = frozenset({"www", "app", "api", "admin", "static", "mail"})
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;display:flex;min-height:100vh;
align-items:center;justify-content:center;margin:0;background:#faf7f2;color:#2b2b2b}}
main{{max-width:28rem;padding:2rem;text-align:center}}h1{{font-size:1.5rem}}</style>
</head><body><main><h1>{title}</h1><p>{message}</p></main></body></html>"""


def tenant_cache_key(subdomain: str) -> str:
    return f"tenant:subdomain:{subdomain}"


async def invalidate_tenant_cache(subdomain: str) -> None:
    """Drop the cached lookup for a subdomain (tenant created / suspended /
    reactivated). Best-effort: a Redis outage must not fail the caller's
    already-committed write — the entry then simply ages out on its TTL."""
    try:
        await cache_service.delete(tenant_cache_key(subdomain.lower()))
    except Exception as exc:
        logger.warning("tenant.cache.invalidate_failed", error=type(exc).__name__)


def extract_subdomain(host_header: str | None) -> str | None | bool:
    """Return the tenant label from a Host header.

    `None`  -> not a tenant host (apex, reserved label, other domain, IP)
    `False` -> under BASE_DOMAIN but not a valid single-label slug (unknown)
    `str`   -> the candidate slug
    """
    if not host_header or host_header.startswith("["):  # missing / IPv6 literal
        return None
    host = host_header.split(":", 1)[0].strip().lower().rstrip(".")
    base = settings.BASE_DOMAIN.strip().lower().rstrip(".")
    if not base or not host.endswith("." + base):
        return None
    label = host[: -(len(base) + 1)]
    if label in RESERVED_SUBDOMAINS:
        return None
    if not _SLUG_RE.match(label):  # also rejects nested labels ("a.b")
        return False
    return label


class TenantLookupUnavailable(Exception):
    """The tenant registry could not be read (cache miss + DB failure)."""


async def resolve_tenant(subdomain: str) -> dict | None:
    """`{"id": str, "name": str, "active": bool}` or None when unknown."""
    key = tenant_cache_key(subdomain)
    try:
        cached = await cache_service.get(key)
    except Exception as exc:
        logger.warning("tenant.cache.read_failed", error=type(exc).__name__)
        cached = None
    if cached is not None:
        return json.loads(cached) or None

    try:
        # restaurant.tenants carries no RLS policy (it is the tenant registry),
        # so this lookup needs no tenant context.
        async with async_session_factory() as session:
            row = (
                await session.execute(
                    select(Tenant.id, Tenant.name, Tenant.is_active).where(
                        Tenant.subdomain == subdomain
                    )
                )
            ).first()
    except Exception as exc:
        logger.error("tenant.lookup.failed", error=type(exc).__name__)
        raise TenantLookupUnavailable from exc

    if row is None:
        tenant, ttl = None, TENANT_NEGATIVE_CACHE_TTL_SECONDS
    else:
        tenant = {"id": str(row.id), "name": row.name, "active": bool(row.is_active)}
        ttl = TENANT_CACHE_TTL_SECONDS
    try:
        await cache_service.set(key, json.dumps(tenant), ttl=ttl)
    except Exception as exc:
        logger.warning("tenant.cache.write_failed", error=type(exc).__name__)
    return tenant


class SubdomainTenantMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        state["tenant_id"] = None

        host = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), None
        )
        subdomain = extract_subdomain(host)
        if subdomain is None:
            await self.app(scope, receive, send)
            return

        tenant = None
        if subdomain is not False:
            try:
                tenant = await resolve_tenant(subdomain)
            except TenantLookupUnavailable:
                await self._reject(scope, receive, send, 503, "TENANT_LOOKUP_UNAVAILABLE",
                                   "Something went wrong. Please try again shortly.",
                                   "Temporarily unavailable", close_code=1013)
                return

        if tenant is None:
            logger.info("tenant.subdomain.not_found")
            await self._reject(scope, receive, send, 404, "TENANT_NOT_FOUND",
                               "We couldn't find that restaurant. Check the address and try again.",
                               "Restaurant not found", close_code=WS_CLOSE_NOT_FOUND)
            return
        if not tenant["active"]:
            logger.info("tenant.subdomain.suspended", tenant_id=tenant["id"])
            await self._reject(scope, receive, send, 403, "TENANT_SUSPENDED",
                               "This restaurant's account is currently suspended. "
                               "Please contact QuickBite support.",
                               "Account suspended", close_code=WS_CLOSE_SUSPENDED)
            return

        state["tenant_id"] = uuid.UUID(tenant["id"])
        await self.app(scope, receive, send)

    async def _reject(
        self, scope: Scope, receive: Receive, send: Send, status_code: int,
        code: str, message: str, title: str, close_code: int,
    ) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": close_code})
            return
        headers = {"Retry-After": "5"} if status_code == 503 else None
        path = scope.get("path", "")
        accept = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"accept"), ""
        )
        if path.startswith("/api/") or "text/html" not in accept:
            response = JSONResponse(
                {"error": {"code": code, "message": message}},
                status_code=status_code, headers=headers,
            )
        else:
            response = HTMLResponse(
                _PAGE.format(title=html.escape(title), message=html.escape(message)),
                status_code=status_code, headers=headers,
            )
        await response(scope, receive, send)
