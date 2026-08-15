"""QuickBite — Redis-backed platform feature flags.

Reads flags from Redis so a flag can be flipped without a deploy — which is
the point of REVIEW-01's "Gemini fallback via Redis feature flag": when OpenAI
degrades at 9pm on a Friday, someone toggles a key and traffic moves.

`static.feature_flags` (models/static_data.py) is the durable record; Redis is
the hot path. Nothing here writes to Redis — seeding and admin toggles belong
to the Super Admin surface (ADMIN-01). This module only reads, and it fails
open to `default` so a Redis outage never becomes an outage of whatever the
flag gates.
"""

import structlog

from app.core import cache_service

logger = structlog.get_logger(__name__)

KEY_PREFIX = "feature_flag:"

# Gates the Gemini fallback. Default True: OpenAI failing with no fallback is
# a 503 for the diner, so the fallback has to be opt-out, not opt-in.
AI_GEMINI_FALLBACK = "ai_gemini_fallback"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "disabled"})


async def is_enabled(flag: str, *, default: bool) -> bool:
    """Read a boolean flag from Redis, falling back to `default`.

    Fails open on both an unset key and an unreachable Redis: a flag lookup is
    never the reason a request fails. An unparseable value is treated as unset
    and logged, because guessing at "maybe" is worse than the documented default.
    """
    try:
        raw = await cache_service.get(f"{KEY_PREFIX}{flag}")
    except Exception as exc:
        logger.warning("feature_flag.lookup_failed", flag=flag, error=str(exc))
        return default

    if raw is None:
        return default

    normalised = raw.strip().lower()
    if normalised in _TRUE_VALUES:
        return True
    if normalised in _FALSE_VALUES:
        return False

    logger.warning("feature_flag.unparseable", flag=flag, value=normalised)
    return default
