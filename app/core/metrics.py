"""QuickBite — Prometheus counters + `/metrics` exposition (SEC-29).

Four counters, one per metric the pre-launch checklist and Doc 6's SEC-29 row
name explicitly: `failed_login_rate`, `jwt_revocations`, `fraud_scan_count`,
`otp_delivery_failure_rate`. Each is incremented at its single call site in
the relevant service, not inferred after the fact from logs — see the
`incr_*` call sites in `app/api/v1/dependencies/auth.py` (via the middleware
below), `app/services/auth_service.py`, `app/services/loyalty_service.py`,
and `app/services/customer_otp_service.py`.

This module intentionally has no dependency on whether Sentry
(`app/core/observability.py`) is configured — Prometheus/Grafana and Sentry
are two independent alerting paths per Doc 6's SEC-29 row, and either must
work with the other disabled.
"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

AUTH_FAILED_LOGINS_TOTAL = Counter(
    "quickbite_auth_failed_logins_total",
    "Auth-related 401 responses (any endpoint) — feeds the failed_login_rate "
    "Grafana panel and the Sentry >50/min alert (SEC-29).",
)

JWT_REVOCATIONS_TOTAL = Counter(
    "quickbite_jwt_revocations_total",
    "Access tokens revoked, either individually (logout) or as a session-wide "
    "sweep (logout-all / refresh-reuse detection). A sweep counts once, not "
    "once per session invalidated — this tracks revocation *events*, not rows.",
    ["reason"],  # "logout" | "logout_all" | "refresh_reuse_detected"
)

FRAUD_SCAN_TOTAL = Counter(
    "quickbite_fraud_scan_total",
    "Loyalty QR scans flagged fraudulent (outside geofence) — StampLog rows "
    "written with is_fraudulent=True.",
)

OTP_DELIVERY_FAILURE_TOTAL = Counter(
    "quickbite_otp_delivery_failure_total",
    "Customer OTP sends where the provider (Twilio/2Factor/SendGrid) "
    "reported delivery failure — feeds otp_delivery_failure_rate.",
    ["channel"],  # "sms" | "email"
)

OTP_VERIFY_FAILURE_TOTAL = Counter(
    "quickbite_otp_verify_failure_total",
    "Customer OTP verification failures (wrong code, lockout, or expired) — "
    "feeds the Prometheus 'OTP failed rate > 100/min' alert (Doc 6 SEC-29).",
    ["reason"],  # "invalid" | "too_many_attempts" | "expired"
)


def render_latest() -> tuple[bytes, str]:
    """Body + content-type for the `/metrics` endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
