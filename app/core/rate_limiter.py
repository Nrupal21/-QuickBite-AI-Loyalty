"""QuickBite — SlowAPI rate limiting: IP + tenant + OTP-specific limits.

Every public endpoint needs a rate limit. One shared Limiter instance so
main.py registers a single exception handler; OTP endpoints add stricter
per-phone limits on top (NEW-OTP-01).

ADMIN-01: the registered handler also best-effort counts a 429 against the
caller's tenant (api_429:{tenant_id}:{yyyymmdd}, an 8-day rolling window,
Redis-only — same non-persisted convention as the api_calls counter in
api/v1/dependencies/auth.py). Attribution is only possible for a route that
authenticates before it rate-limits — a pre-auth 429 (login, OTP request)
has no tenant yet and is silently not counted, which is a known, accepted
gap: those flows are IP-rate-limited, not tenant-rate-limited, so a
per-tenant trip count wouldn't mean anything for them anyway.

Counting is restricted to staff/owner (SubjectType.USER) principals, to
match the population `api_calls` measures: `get_current_user()` — the only
place `api_calls` is incremented — raises before it ever reaches a Customer
principal, so counting a Customer's 429 here would inflate `api_429` with
diner traffic that has no corresponding `api_calls` entry, producing an
impossible rate_limited_count > request_count on the monitors dashboard.
"""

from datetime import datetime, timezone

import structlog
from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core import cache_service
from app.core.principal import SubjectType

logger = structlog.get_logger(__name__)

limiter = Limiter(key_func=get_remote_address)

# Throttles the Redis-outage warning below to at most once every 60s, so a
# sustained outage doesn't flood logs with a warning on every single 429.
_last_api_429_warning_at: datetime | None = None


async def rate_limit_exceeded_handler_with_tracking(request: Request, exc: RateLimitExceeded):
    global _last_api_429_warning_at
    principal = getattr(request.state, "principal", None)
    if (
        principal is not None
        and principal.subject_type is SubjectType.USER
        and principal.tenant_id is not None
    ):
        try:
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            await cache_service.incr(f"api_429:{principal.tenant_id}:{day}", ttl=8 * 86400)
        except Exception:  # noqa: BLE001 — analytics counter, never fatal
            now_ts = datetime.now(timezone.utc)
            if (
                _last_api_429_warning_at is None
                or (now_ts - _last_api_429_warning_at).total_seconds() >= 60
            ):
                logger.warning("admin.api_429_counter_failed", tenant_id=str(principal.tenant_id))
                _last_api_429_warning_at = now_ts
    return _rate_limit_exceeded_handler(request, exc)
