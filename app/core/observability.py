"""QuickBite — Sentry initialisation with PII scrubbing (SEC-29, checklist #16).

`sentry-sdk` is a **production-only** dependency (`requirements/prod.txt`) —
`init_sentry()` imports it lazily, inside the `if settings.SENTRY_DSN:`
branch, so a dev/CI box that only installed `requirements/dev.txt` (no
`sentry-sdk`) never attempts the import. `SENTRY_DSN` defaults to `""`, so
this is a silent no-op everywhere except a deployment that actually
configured it.

The `before_send` hook exists because Sentry captures request bodies and
breadcrumbs by default — and this app's request bodies routinely contain a
customer's phone/email (`identifier`), a live OTP code, or a bearer token.
Without scrubbing, an error captured mid-request would ship that PII to
Sentry's servers, defeating the AES-256-GCM-at-rest guarantee this codebase
otherwise holds everywhere else (AGENTS.md §3).
"""

from typing import Any

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Request/breadcrumb data keys that must never leave this process. Matched
# case-insensitively against dict keys at any depth of `event["request"]`
# and `event["extra"]`. Deliberately broad — a false-positive redaction (e.g.
# a field that happens to be named "email" but held nothing sensitive) costs
# nothing; a missed one ships PII to a third party.
_SENSITIVE_KEYS = frozenset(
    {
        "identifier",
        "otp_code",
        "phone",
        "phone_number",
        "email",
        "password",
        "totp_code",
        "refresh_token",
        "access_token",
        "authorization",
        "gps_lat",
        "gps_lng",
        "encrypted_phone",
        "encrypted_email",
    }
)
_REDACTED = "[REDACTED]"


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _REDACTED if key.lower() in _SENSITIVE_KEYS else _scrub(val)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def scrub_pii_before_send(event: dict, hint: dict) -> dict | None:  # noqa: ARG001
    """Sentry `before_send` hook — redact known-sensitive keys, never drop
    the whole event (an unscrubbed-but-visible error is still useful; a
    silently dropped Critical error defeats SEC-29's alerting purpose)."""
    if "request" in event:
        event["request"] = _scrub(event["request"])
    if "extra" in event:
        event["extra"] = _scrub(event["extra"])
    for breadcrumb in event.get("breadcrumbs", {}).get("values", []):
        if "data" in breadcrumb:
            breadcrumb["data"] = _scrub(breadcrumb["data"])
    return event


def init_sentry() -> None:
    """No-op unless `SENTRY_DSN` is configured. Call once, at import time,
    from `app/main.py` — before the app starts handling requests."""
    if not settings.SENTRY_DSN:
        return

    import sentry_sdk  # noqa: PLC0415 — see module docstring: prod-only dependency

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        before_send=scrub_pii_before_send,
        # Request bodies are exactly what before_send above scrubs — capture
        # them (so a genuine bug is still debuggable) rather than disabling
        # request-body capture outright, which would also blind us to
        # non-PII fields (plan_id, branch_id, tags) that matter for triage.
        send_default_pii=False,
        traces_sample_rate=0.1,
    )
    logger.info("sentry.initialized", environment=settings.ENVIRONMENT)
