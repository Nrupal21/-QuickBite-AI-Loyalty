# Sentry Alert Rules (SEC-29)

Sentry alert rules are project configuration, not application code — there is
no file the app reads to define them. This document is the source of truth
for what to configure in the Sentry project dashboard (Settings → Alerts),
so the setup is reproducible and reviewable even though it isn't literally a
config file the app loads.

**Prerequisite:** `SENTRY_DSN` must be set in the deployment environment —
`app/core/observability.py::init_sentry()` is a no-op otherwise (see that
module's docstring). Confirm events are actually arriving in the Sentry
project before configuring alerts against it.

## Alert 1 — Auth Failure Spike

Doc 6 SEC-29: *"Sentry alert: > 50 auth-related 401s/minute → PagerDuty
notification"*.

- **Type:** Issue Alert (metric alert on a custom tag, or — simpler — an
  Alertmanager webhook receiving the equivalent Prometheus rule in
  `prometheus_alerts.yml`'s `QuickBiteHighFailedLoginRate`, forwarded into
  Sentry as an integration). Prefer the Prometheus route if both are
  available, since 401s are page-view/response-status events which Sentry's
  APM captures less naturally than a request-count metric a middleware emits
  directly (see `app/main.py::count_auth_failures`).
- **If configuring natively in Sentry instead:** tag every captured
  `HTTPException(status_code=401)` event (or a breadcrumb) with
  `auth_failure: true` inside `scrub_pii_before_send` or a dedicated
  `before_send` addition, then alert on `count() > 50` of events matching
  that tag `in 1 minute`.
- **Action:** PagerDuty notification to the on-call Security Dev rotation.
- **Runbook link:** attach `docs/RUNBOOK.md` §1 (Compromised JWT) as the
  alert's linked runbook.

## Alert 2 — Elevated Error Rate (general)

Not explicitly required by SEC-29's acceptance criteria, but standard
practice once Sentry is wired: alert when the overall unhandled-exception
rate exceeds baseline (Sentry's built-in "issue frequency" alert type, e.g.
"an issue is seen more than 100 times in 1 hour").

## Verification (blocking sign-off — see LAUNCH_SIGNOFF.md #16)

Before this can be marked done:

1. Confirm `before_send` (`scrub_pii_before_send`) is actually scrubbing in
   the live project — trigger a test error with a fake phone/OTP in the
   request body and confirm the captured event in Sentry's UI shows
   `[REDACTED]`, not the raw value.
2. Run SEC-29's own acceptance test: 200 failed auth attempts against
   staging → alert fires within 2 minutes.
3. Confirm the PagerDuty integration actually pages someone, not just logs
   to a Slack channel nobody watches at 3am.

## Not yet configured (out of scope for this pass)

- The PagerDuty integration itself (requires a PagerDuty account/API key not
  available in this repo).
- Sentry project creation/DSN provisioning (infra step, not app code).
