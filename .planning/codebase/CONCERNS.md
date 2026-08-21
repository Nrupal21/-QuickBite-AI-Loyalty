# Codebase Concerns

**Analysis Date:** 2026-08-19

## Tech Debt

**Outbox Table Missing RLS Protection (SEC-04):**
- Issue: `app/db/models/outbox.py` (ProjectionOutbox) is deliberately not protected by Row-Level Security. The table is scanned by the Celery drain worker which must read across all tenants in one query, but RLS's single-tenant-per-session model cannot express this.
- Files: `app/db/models/outbox.py`, `app/services/projection_service.py`
- Impact: Any session holding the app role can read every tenant's pending payment payloads. While payloads contain already-committed ledger facts (not credentials), the exposure is real and undesirable.
- Fix approach: Enable RLS and have the drain worker read through a SECURITY DEFINER function owned by `quickbite_bootstrap` (same pattern as credential resolvers in migration 0007). This belongs with the async Celery work — the drain's event-loop and pooling story is unproven, and changing both at once would make failures hard to attribute.

**Anti-Fraud Module Incomplete (LOYALTY-03 Concern):**
- Issue: `app/core/anti_fraud.py` contains only a docstring describing fraud detection for loyalty scans (impossible travel, rapid-fire scans, device fingerprint mismatches, geographic impossibilities) but has no implementation.
- Files: `app/core/anti_fraud.py`, `app/services/loyalty_service.py`
- Impact: Loyalty scan fraud detection is missing; the system accepts all scans without anomaly checks. An attacker could farm stamps or bypass geofence restrictions.
- Fix approach: Implement the four detection patterns described in the docstring. Hook into `LoyaltyService.process_scan()` before stamp recording. Use Redis to track per-customer/IP scan history and geographic coordinates to detect impossible travel.

**Missing Invite Email Landing Page (STITCH-13):**
- Issue: `app/api/v1/routers/team.py` sends team invite emails with links via `accept_url` (line 45), but no landing page serves the invite-acceptance flow.
- Files: `app/api/v1/routers/team.py`, `app/templates/` (missing invite template)
- Impact: Team members receive invite emails but cannot accept them; the flow is broken.
- Fix approach: Create `app/templates/team/invite_accept.html` (Stitch.ai generated) with a form that accepts the invite token and POST to the /auth/accept-invite endpoint.

**Incomplete Loyalty Card Endpoints (LOYALTY-01/02):**
- Issue: Loyalty QR card routes `/loyalty/card/{id}` and `/loyalty/menu/{branch_id}` are documented as TODO but not implemented.
- Files: `app/api/v1/routers/loyalty.py` (line 10), `app/schemas/loyalty.py` (line 8)
- Impact: Customers cannot view their current loyalty card or branch-specific menu/rewards. The feature is partially built (QR scan and redemption exist) but the display layer is missing.
- Fix approach: Implement endpoints to fetch and render loyalty card state and branch-specific reward tiers/rules. Add schemas `LoyaltyCardResponse` and `BranchMenuResponse`.

**Scratch Card Model Not Implemented (LOYALTY-NICE):**
- Issue: `app/db/models/loyalty.py` line 21 TODO marks ScratchCard model as unimplemented, a nice-to-have loyalty feature.
- Files: `app/db/models/loyalty.py`, `app/db/migrations/` (no scratch card migration)
- Impact: Scratch card rewards (a secondary loyalty mechanism) cannot be provisioned or tracked.
- Fix approach: Add migration to create `loyalty.scratch_cards` and `loyalty.scratch_card_scratches` tables. Implement ScratchCard and ScratchCardScratch models with corresponding service methods.

---

## Known Bugs

**Pytest Langsmith Plugin Conflict:**
- Symptoms: Test collection fails with langsmith plugin errors when running `pytest` without `-p no:langsmith_plugin`.
- Files: `pyproject.toml` (line 27)
- Trigger: `pytest` command without the flag; a globally installed langsmith package conflicts with Pydantic v2 under Python 3.12.
- Workaround: Use pytest command with `-p no:langsmith_plugin` flag; this is already configured in `pyproject.toml` addopts.

---

## Security Considerations

**External Token Tenant Resolution Relies on Webhook Notes (Billing):**
- Risk: Razorpay webhook tenant resolution reads `notes.tenant_id` from the webhook payload itself rather than querying the database (to avoid RLS chicken-and-egg). An attacker who crafts a webhook with a forged `tenant_id` note could potentially route a payment to the wrong tenant.
- Files: `app/services/billing_service.py` (lines 6-16), webhook handler in `app/api/v1/routers/billing.py`
- Current mitigation: Razorpay's signature verification (HMAC-SHA256) via `razorpay_signature.verify()` prevents tampering with the webhook payload. Notes are echoed back verbatim from the checkout creation request, so they come from the server that created the subscription in the first place.
- Recommendations: Continue to verify every webhook signature before processing. Add audit logging of tenant_id from notes to detect spoofing attempts. Consider adding an extra tenant_id lookup via a SECURITY DEFINER function as a secondary check once the drain worker architecture stabilizes.

**JWT Algorithm Confusion Prevention:**
- Risk: Token verification must reject algorithm confusion attacks (e.g., an HS256 token claiming a Supabase issuer, then using the JWKS public key as an HMAC secret).
- Files: `app/api/v1/dependencies/auth.py` (lines 80-84)
- Current mitigation: Per-provider algorithm allowlists (`_ALG_BY_PROVIDER`) ensure each verifier only accepts the algorithms its provider uses. Supabase and Firebase use RS256/ES256 (asymmetric); local tokens use HS256.
- Recommendations: Maintain strict per-provider algorithm checks. Continue to require explicit algorithm declaration per provider. Never add new providers without specifying their allowed algorithms.

**External Auth JIT Provisioning Disabled by Default (EXTERNAL_AUTH_JIT_ENABLED):**
- Risk: Staff identities are never created just-in-time via external auth (Supabase/Firebase) — they require explicit linking via `POST /auth/link/{provider}`. Customers, however, may have JIT provisioning enabled via the feature flag, which could open registration to anyone with a verified email in the external auth provider.
- Files: `app/core/config.py` (line 160), `app/services/identity_link_service.py`
- Current mitigation: JIT is disabled by default (`EXTERNAL_AUTH_JIT_ENABLED=false`). Email is never a join key unless the provider marks it as verified. Staff accounts require proof of current local session via link endpoint.
- Recommendations: Keep JIT disabled in production. If enabling for customers, audit the external auth provider's email verification flow. Do not JIT provision staff accounts under any circumstances.

---

## Performance Bottlenecks

**auth_service.py File Size and Complexity:**
- Problem: `app/services/auth_service.py` is 1189 lines, covering registration, login, MFA, refresh, logout, password reset, and become-restaurant all in one file. Complex logic like MFA session management and token rotation makes this file fragile to modify.
- Files: `app/services/auth_service.py`
- Cause: Multiple overlapping concerns (email verification, password hashing, MFA enrollment, tenant creation) bundled into one service class.
- Improvement path: Refactor into separate services: `registration_service.py` (register/verify), `login_service.py` (login/MFA), `session_service.py` (refresh/logout/revocation). This reduces each file to ~300-400 lines and isolates concerns.

**Identity Link Service Complexity (502 lines):**
- Problem: `app/services/identity_link_service.py` handles external provider identity mapping with intricate rules around email verification, JIT provisioning, and staff vs. customer flows.
- Files: `app/services/identity_link_service.py`
- Cause: Multiple identity-provider branches (Firebase, Supabase) with different verification semantics (email_verified flag, phone-verified claims) in one module.
- Improvement path: Separate provider-specific logic into `identity_*_service.py` files (firebase_identity, supabase_identity) and delegate from a dispatcher. Test each provider in isolation.

**AI Provider Timeout Budget Tight (1.2 seconds per provider):**
- Problem: `app/services/ai_engine.py` allocates `AI_REQUEST_TIMEOUT_SECONDS` (default 1.2s) per provider, leaving tight margins for fallback. If OpenAI times out at 1.2s, Gemini has only ~1.8s to produce a draft within the 3s end-to-end budget (REVIEW-01).
- Files: `app/services/ai_engine.py`, `app/core/config.py` (line 82)
- Cause: Network latency, SSL handshake, and model inference time all consume the budget. A slow DNS lookup or high provider load can waste the timeout without any output.
- Improvement path: Consider increasing per-provider timeout to 1.5s and reducing end-to-end budget from 3s to 3.5s if possible. Alternatively, pre-warm OpenAI connection via a health check on startup. Implement circuit breaker to stop calling failed providers for 5 minutes.

**Celery Task Async Bridging with asyncio.run():**
- Problem: `app/workers/tasks.py` uses `asyncio.run()` inside sync Celery tasks to call async service code. This creates a new event loop per task, which is correct but adds overhead.
- Files: `app/workers/tasks.py` (lines 56, 75, 99)
- Cause: Celery 5.3 is sync-by-default; async-first Celery is not released. The pattern is safe but not optimal.
- Improvement path: Once Celery 6+ ships with native async support, migrate to `@celery_app.task(bind=True, max_retries=3)` with `async def` and remove `asyncio.run()` wrapping. For now, this is the standard pattern and acceptable.

---

## Fragile Areas

**Token Dispatch Across Multiple Auth Providers (dependencies/auth.py):**
- Files: `app/api/v1/dependencies/auth.py` (348 lines), `app/core/principal.py`
- Why fragile: `resolve_principal()` must classify an incoming token by its `iss` claim, then route it to the correct verifier (Local, Supabase, Firebase). A typo in an issuer URL, a missing JWKS cache refresh, or a provider configuration change silently breaks one login method while others still work. The code doesn't fail fast — it returns a generic 401, making the root cause hard to spot in production.
- Safe modification: Always test token dispatch with a test token from each provider. Add detailed error logging with the actual issuer and expected issuer. Write integration tests that mock each provider's JWKS response. Validate issuer URLs in config at startup.
- Test coverage: 348 lines, but test coverage for each provider path (Local/Supabase/Firebase) is split across `test_token_dispatch.py`, `test_supabase_auth.py`, `test_firebase_credentials_config.py` — ensure all three verifiers are exercised in a single test.

**RLS Context Management (db/rls.py and dependencies):**
- Files: `app/db/rls.py` (136 lines), `app/api/v1/dependencies/auth.py`, `app/api/v1/dependencies/customer_auth.py`, `app/db/session.py`
- Why fragile: `set_tenant_context()` must be called before ANY query on an RLS-protected table, or the query silently returns zero rows (not an error). A service that calls `session.commit()` mid-request must have tenant context re-applied by the `after_begin` listener (`_reapply_tenant_context`), or queries after the commit see no rows. The RLS policy is enforced by PostgreSQL, not by Python, so a bug only surfaces as "zero results" in production, not a raised exception.
- Safe modification: Always call `set_tenant_context()` in dependency code before passing the session to a service. Never commit inside a service unless you must — batch writes and commit once at the route level. Write tests that verify row counts before and after commits.
- Test coverage: Tests should mock `rls.set_tenant_context()` and `rls.clear_tenant_context()` to verify they are called in the right order. Verify that a query after a commit inside a service still sees rows from the right tenant.

**Firestore Projection Outbox Drain (services/projection_service.py):**
- Files: `app/db/models/outbox.py` (ProjectionOutbox), `app/services/projection_service.py`, `app/workers/tasks.py` (drain_projection_outbox)
- Why fragile: The outbox drain must read every tenant's pending projection rows without RLS enabled (to allow a single query across tenants). If the drain task crashes mid-batch or Firestore becomes unreachable, rows stay `pending` and the projection mirror falls behind Postgres. The next drain run will retry, but there's a window of inconsistency. A Firestore credential rotation or quota exhaustion silently stops the drain with only a log line to show for it.
- Safe modification: Add detailed logging of drain progress (rows scanned, rows sent, errors). Implement a healthcheck endpoint that alerts if the drain hasn't succeeded in the last hour. Set up Firestore quota alerts. Test drain error recovery by mocking Firestore failures.
- Test coverage: Test drain with a healthy Firestore, with transient failures (simulate retry), and with permanent failures (simulate max_attempts exhaustion). Verify that a failed row doesn't block subsequent rows from draining.

**Email Template Rendering with Safe String Formatter (notification_service.py):**
- Files: `app/services/notification_service.py` (225 lines), `app/core/config.py` (EMAIL_TEMPLATE_CACHE_TTL_SECONDS, line 111)
- Why fragile: Template bodies are stored in the database and rendered via `_SafeFormatter`, which blocks attribute/index access. However, if a template context dict is built incorrectly with nested objects, or if a plaintext field name happens to match an object attribute, the formatter will substitute it incorrectly. A Super Admin editing a template can accidentally break all notifications for a channel if they use unsupported syntax.
- Safe modification: Document the template syntax clearly (plain `{name}` fields only). Add validation when updating templates to reject unsupported placeholders. Log template rendering errors with the template name and context for debugging.
- Test coverage: Test template rendering with various context shapes — some missing keys, some with nested objects (should fail gracefully). Test all built-in templates in DEFAULTS to ensure they don't use disallowed syntax.

---

## Scaling Limits

**Redis Cache for OTP and Rate Limiting:**
- Current capacity: Redis (Upstash) shared across OTP codes, feature flags, RLS tenant context (per-request), email template cache, and AI draft cache.
- Limit: Upstash free tier is ~100K operations/month or shared memory (check settings). A production deployment with thousands of concurrent users will exhaust memory quickly if OTP retention (300s) and rate-limit windows (per-minute) overlap.
- Scaling path: Segregate Redis namespaces by use case (cache_service with TTLs for OTP, Redis cluster for rate limiting, separate cache for AI drafts). Monitor memory usage and eviction policy. Consider splitting Upstash subscription tier or self-hosting Redis on the deployment platform.

**PostgreSQL Connection Pool Under Transaction Pooler (DB_USE_TRANSACTION_POOLER):**
- Current capacity: When using a transaction pooler (Supabase, PgBouncer), asyncpg prepared statement cache is disabled (`statement_cache_size: 0`) to prevent "prepared statement does not exist" errors.
- Limit: Every query becomes a fresh parse on the server, increasing CPU load. Load testing needed to find the sweet spot between pooler transaction count and query parsing overhead.
- Scaling path: Profile queries under load. If parsing becomes a bottleneck, use named prepared statements in Alembic migrations (PostgreSQL PREPARE STATEMENT) rather than asyncpg's cache. Or upgrade to a dedicated PostgreSQL instance (not through a pooler) and re-enable the statement cache.

**Firestore Projection Mirror Consistency:**
- Current capacity: `projection_service.drain_pending()` is called every few seconds (Celery beat scheduler). Each run scans all `PENDING` rows and writes them to Firestore.
- Limit: Firestore quota is (plan-dependent) ~1000 writes/sec. A large multi-tenant deployment with frequent subscription updates could hit the quota and stall the mirror. Payment/invoice queries on Firestore will return stale data.
- Scaling path: Add batching to reduce number of Firestore writes (batch multiple events per document write). Implement quota-aware backoff in the drain worker. Set up Firestore quota alerts to warn before exhaustion.

---

## Dependencies at Risk

**OpenAI API Timeout Dependency (REVIEW-01):**
- Risk: `app/services/ai_engine.py` falls back to Gemini if OpenAI times out, but if both providers are down, the draft endpoint returns 503 and the review workflow blocks. The 3-second end-to-end budget is tight; any network lag makes both timeouts likely.
- Impact: Customers cannot draft reviews; managers cannot generate responses. The feature degrades entirely.
- Migration plan: Implement a circuit breaker pattern — if OpenAI fails 5 times in a row, stop calling it for 5 minutes and go straight to Gemini. Log circuit breaker trips so ops can investigate. Add a feature flag to disable OpenAI entirely and force Gemini-only mode for rapid recovery.

**Supabase JWKS Cache (SUPABASE_JWKS_TTL_SECONDS):**
- Risk: Supabase publishes JWKS at `https://{ref}.supabase.co/auth/v1/.well-known/jwks.json`, cached for 600 seconds (10 minutes, per `config.py` line 135). If a key is revoked and `quibikite_app` caches the old JWKS for 10 minutes, tokens signed with the revoked key will still verify as valid.
- Impact: A compromised Supabase signing key (unlikely but possible) would stay trusted in this app for up to 10 minutes after revocation.
- Migration plan: Reduce SUPABASE_JWKS_TTL_SECONDS to 300 (5 minutes) or add a `jwks_refresh_on_error` flag that forces a cache refresh if a token fails verification. Monitor Supabase status page for key rotation events.

**Firebase Service Account Credentials in Environment (FIREBASE_SERVICE_ACCOUNT_JSON):**
- Risk: The entire Firebase service account (a JSON blob containing a private key) is passed as an environment variable. If the .env file or container image is ever leaked, the attacker gains admin access to Firebase, Firestore, and all payment data.
- Impact: Full compromise of Firestore projection and potentially ID token verification.
- Migration plan: Move Firebase credentials to a secrets manager (GCP Secret Manager, HashiCorp Vault, AWS Secrets Manager). Load them at runtime rather than at import time. Rotate credentials every 90 days.

**Razorpay Webhook Secret (RAZORPAY_WEBHOOK_SECRET):**
- Risk: Webhook signature verification depends on this secret. If leaked, an attacker can forge webhook events and manipulate subscriptions, invoices, and billing state.
- Impact: Unauthorized subscription cancellations, false payment confirmations, billing fraud.
- Migration plan: Rotate Razorpay API keys immediately if compromised. Implement additional verification (e.g., query Razorpay API to confirm webhook event) as a secondary check. Log webhook verification failures with rate-limit detection to spot attacks.

---

## Missing Critical Features

**Geofence Fraud Detection (Loyalty Scanning):**
- Problem: `app/core/anti_fraud.py` is a stub. Loyalty scans have no fraud detection; an attacker can farm stamps or bypass location checks.
- Blocks: LOYALTY-03's acceptance criterion "Scans must be gated by geofence and antifraud checks."
- Fix path: Implement the four checks in the docstring (impossible travel, rapid-fire scans, device fingerprint, geographic impossibility). Use Redis to track per-customer scan history. Integrate into `LoyaltyService.process_scan()` before stamp recording.

**Team Invite Acceptance UI (STITCH-13):**
- Problem: Team invite email links to an acceptance page that doesn't exist. Invites are sent but cannot be accepted.
- Blocks: TEAM-02's acceptance criterion "Team members receive invite email and can accept it."
- Fix path: Create Stitch.ai-generated HTML page at `app/templates/team/invite_accept.html`. Backend route should already exist (`POST /auth/accept-invite`); just wire the frontend.

**Loyalty Card Display (LOYALTY-01/02):**
- Problem: Customers can scan QR and redeem, but cannot view their loyalty card or branch-specific reward tiers.
- Blocks: LOYALTY-01's acceptance criterion "Customers see their loyalty card with stamp count and next reward."
- Fix path: Add endpoints `/loyalty/card/{id}` and `/loyalty/menu/{branch_id}` to fetch and render card state. Wire Stitch-generated UI template.

---

## Test Coverage Gaps

**Anti-Fraud Module Untested (Loyalty Scanning):**
- What's not tested: Fraud detection is unimplemented, so no tests exist. The entry point `LoyaltyService.process_scan()` has no fraud checks.
- Files: `app/core/anti_fraud.py`, `app/services/loyalty_service.py`
- Risk: Fraudulent scans are never caught. An attacker can collect unlimited stamps with no rate limiting beyond the 6 scans/hour IP-based limit.
- Priority: HIGH — fraud detection is a stated requirement (REVIEW-01 acceptance criterion).

**Firestore Projection Drain Error Recovery Gaps:**
- What's not tested: Drain worker behavior when Firestore is unreachable, when rows hit MAX_ATTEMPTS (10), and when a row fails mid-batch but others succeed.
- Files: `app/workers/tasks.py`, `app/services/projection_service.py`, `tests/integration/test_projection_outbox.py` (if it exists)
- Risk: Silent drain failure with stale Firestore data. No alerting if the mirror falls behind.
- Priority: MEDIUM — production impact is high but the drain is resilient (rows stay pending and retry).

**RLS Tenant Context Re-application After Commit:**
- What's not tested: A service that commits mid-request, then queries again, should still see rows from the same tenant. The `after_begin` listener in `db/rls.py` must restore tenant context.
- Files: `app/db/rls.py`, tests should cover this in integration tests.
- Risk: Queries after mid-request commits silently return zero rows (RLS filters instead of errors), making the bug invisible until production.
- Priority: HIGH — RLS is the core multi-tenancy isolation mechanism.

**Token Dispatch to Multiple Auth Providers:**
- What's not tested: Full end-to-end token verification from each provider (Local, Supabase, Firebase) with mismatched algorithm detection.
- Files: `app/api/v1/dependencies/auth.py`, `tests/unit/test_token_dispatch.py` (should exist)
- Risk: A typo in issuer config, a missing JWKS refresh, or a provider outage breaks one login method silently (returns 401 instead of a specific error).
- Priority: MEDIUM — fragile but well-isolated; errors are caught at the auth boundary.

---

*Concerns audit: 2026-08-19*
