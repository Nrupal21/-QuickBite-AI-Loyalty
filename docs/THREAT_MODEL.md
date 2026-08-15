# QuickBite AI + Loyalty — Threat Model

**Ticket:** SEC-01 · **Owner:** Security Dev · **Status:** Draft — pending Backend Dev review
**Scope:** All attack surfaces reachable by an external caller (customer, restaurant staff,
or an unauthenticated attacker) against the FastAPI backend, its background workers, and its
direct third-party integrations. Internal infra hardening (CI toolchain, Docker, network
policy) is out of scope here — see SEC-02.

This document reflects the **current state of the code on this branch**, not the original
Doc 3 design. Two things drifted since Doc 3 v2.0 was written and are treated as canonical
below:

- Billing is **Razorpay**, not Stripe (`app/core/razorpay_signature.py`,
  `app/services/billing_service.py`). The "Stripe webhook replay" category in the ticket
  description is assessed against the Razorpay implementation that actually exists.
- Authentication is no longer bcrypt+TOTP alone. Three token families now reach the API —
  local HS256, Supabase (ES256/RS256 via JWKS), and Firebase (RS256) — unified through
  `app/core/principal.py` and `app/api/v1/dependencies/auth.py`. Threats below cover all three.

Each threat lists **Likelihood** (L/M/H) and **Severity** (Low/Med/High/Critical) as designed
today, i.e. assuming the mitigation column's code is what actually ships. Where a mitigation
is a stub or not yet wired in, likelihood is rated against the *undefended* state and the gap
is called out explicitly rather than hidden behind an optimistic rating.

---

## 1. OTP Delivery Compromise (SMS / Email)

Customer login (`app/services/customer_otp_service.py`) has no password — a 6-digit code
delivered via Twilio SMS or SMTP email (`app/services/messaging_service.py`) is the entire
credential. Compromising OTP delivery or verification is equivalent to full account takeover.

| # | Threat | Likelihood | Severity | Mitigation | Ticket |
|---|--------|:---:|:---:|-----------|:---:|
| 1.1 | Brute-forcing the 6-digit code (1,000,000 combinations) | M | High | `CUSTOMER_OTP_MAX_ATTEMPTS = 3` — 3rd wrong guess deletes the Redis key and forces a fresh request (`customer_otp_service.verify_otp`) | SEC-07 |
| 1.2 | Predictable/weak OTP generation | L | Critical | `secrets.choice(string.digits)` per digit — CSPRNG, not `random.randint` (`_generate_otp_code`) | SEC-05 |
| 1.3 | OTP theft via DB/Redis compromise (data at rest) | L | High | Only `sha256_hex(otp)` is stored in Redis, TTL `CUSTOMER_OTP_TTL_SECONDS = 300`s, capped at ≤600s by a config validator; plaintext code never touches storage | SEC-05 |
| 1.4 | Phone/email enumeration via response differences | M | Medium | Unknown identifier returns `200 OTPNewUserResponse` (registration flow), never a 404/409 that reveals account existence | SEC-06 |
| 1.5 | OTP request flooding (SMS-bombing a victim's phone, cost abuse) | H | Medium | Per-identifier cooldown key (`otp_cooldown:*`, 120s) + daily cap `CUSTOMER_OTP_DAILY_MAX = 5` via atomic Redis `INCR` | SEC-07 |
| 1.6 | OTP replay after expiry or reuse of a consumed code | L | Medium | Redis key deleted immediately on successful verify and on 3rd failure — a used or expired code can never verify twice (`verify_otp`) | SEC-07 |
| 1.7 | Twilio/SMTP outage silently drops the OTP with no user-visible signal | M | Low | `send_otp_sms`/`send_otp_email` return `bool`; missing Twilio credentials logs `sms.otp.skipped_no_credentials` and returns `False` rather than raising — **gap:** the caller in `request_otp` does not currently branch on this return value to warn the customer | SEC-05 (follow-up needed) |
| 1.8 | SIM-swap interception of SMS OTP | L | High | No SMS-specific mitigation exists (endemic to SMS OTP); email OTP is offered as an alternate channel via `identifier_type` | Accepted risk — Doc 3 §5 |

## 2. JWT Forgery & Replay

Three verifiers converge on `app/core/principal.py::Principal`. A forged or replayed token in
any of the three families is a full auth bypass if it succeeds.

| # | Threat | Likelihood | Severity | Mitigation | Ticket |
|---|--------|:---:|:---:|-----------|:---:|
| 2.1 | `alg:none` forgery | L | Critical | `_classify()` in `dependencies/auth.py` rejects any `alg` outside `_ALLOWED_ALGS`; `jwt.decode` never called with `verify_signature: False` on the trusted path | SEC-03, SEC-18 |
| 2.2 | Algorithm-confusion (RS256 public key replayed as an HS256 secret) | M | Critical | Per-provider algorithm pinning (`_ALG_BY_PROVIDER`): a Supabase-issued RS256/ES256 claim can never be re-verified as LOCAL/HS256, and vice versa, closing the classic "use the public key as the HMAC secret" attack | SEC-03, SEC-08 |
| 2.3 | Header-driven key confusion via `jku`/`x5u` pointing at an attacker-controlled key server | L | Critical | `_classify()` explicitly rejects any token carrying `jku` or `x5u` in the header | SEC-03 |
| 2.4 | Issuer substring spoofing (e.g. `iss: https://evil.com/#https://ref.supabase.co/...`) | L | High | Exact string equality on `iss`, never `startswith`/`in` (`_classify()` comment states this explicitly) | SEC-03 |
| 2.5 | Customer JWT used against owner/staff routes or vice versa | M | High | Separate signing keys (`SECRET_KEY` vs `CUSTOMER_SECRET_KEY`), asserted unequal at config load (`config.py` field validator); `get_current_user` 403s a customer principal, `get_current_customer` 403s a staff principal | SEC-08 |
| 2.6 | Replay of a revoked local session (post-logout, post-password-change) | M | High | Two independent layers: per-`jti` Redis blocklist (`revoked_jti:{jti}`, checked in `_resolve_local`) for immediate single-session revocation, plus `tokens_valid_from` watermark on the User/Customer row (`_assert_not_globally_revoked`) that also covers Supabase/Firebase sessions, which have no server-side per-session handle | SEC-03, SEC-18 |
| 2.7 | Deactivated staff account continues authenticating until token expiry | M | Medium | `_resolve_local` re-checks `user.is_active` from the DB on every request rather than trusting the token claim, closing the window a stateless 15-min JWT would otherwise leave open | SEC-03 |
| 2.8 | `tenant_id` tampered in a JWT claim to cross tenant boundaries | L | Critical | `Principal.tenant_id` is **always** the DB row's tenant, never the claim (`principal.py` docstring states this invariant); `_resolve_tenant_id` for JIT customer provisioning reads the subdomain from `request.state`, never a header or token field | SEC-04, SEC-08 |
| 2.9 | Differentiated error responses used as a provider/algorithm probing oracle | L | Low | Every failure path in `resolve_principal` returns an identical 401 body regardless of which check failed | SEC-03 |
| 2.10 | Supabase `service_role`/`anon` JWT (same signing key as end-user tokens) used to impersonate a user | L | Critical | `verify_supabase_token` explicitly rejects any claim where `role != "authenticated"` | SEC-08 |
| 2.11 | JWKS-fetch throttle abused as a DoS vector (attacker mints tokens with random `kid`s to force origin fetches) | L | Medium | `_MIN_REFETCH_GAP_SECONDS = 60` floor between forced refreshes on an unknown `kid` (`jwks_cache.get_signing_key`) | SEC-08 |

## 3. Row-Level Security (RLS) / Cross-Tenant Data Bypass

Every tenant-scoped table filters on `current_setting('app.tenant_id')`. RLS fails **closed to
zero rows**, not to an error, when context is missing — meaning a bug here is invisible until
specifically tested for.

| # | Threat | Likelihood | Severity | Mitigation | Ticket |
|---|--------|:---:|:---:|-----------|:---:|
| 3.1 | Tenant context never set on an authenticated request → silently returns zero rows (availability bug, not a leak, but masks a leak elsewhere) | M | Medium | `set_tenant_context` called unconditionally in `_resolve_local`/`identity_link_service.resolve` before any subject-table query | SEC-04 |
| 3.2 | Tenant context leaking across requests on a pooled connection | L | Critical | `set_config(..., true)` is transaction-local by design — cannot outlive the transaction (`db/rls.py` docstring); `after_begin` listener re-applies it every new transaction so a mid-request commit doesn't silently drop it | SEC-04, SEC-23 |
| 3.3 | Tenant context leaking across tenants inside a long-lived Celery/outbox worker session | M | High | `tenant_context()` async context manager explicitly clears on exit — required because a worker reuses one session across many tenants' rows | SEC-04, SEC-15 |
| 3.4 | Tenant boundary derived from a client-controlled JWT claim instead of the DB | L | Critical | Explicitly rejected by design — `set_tenant_context` docstring: "must always come from the database... never from a JWT claim" | SEC-04 |
| 3.5 | Webhook handler (Razorpay) writing data with no tenant context at all, bypassing RLS's `WITH CHECK` | L | Medium | `handle_webhook` resolves `tenant_id` from the payload notes and binds it via `rls.set_tenant_context` before the `BillingEvent` INSERT, so a wrong tenant fails the RLS `WITH CHECK` rather than mis-attributing the row | SEC-04, SEC-10 |
| 3.6 | Direct `SELECT` bypassing RLS entirely (RLS not enabled on a table, or disabled by a migration) | L | Critical | Verified per-table via `information_schema` audit against all RLS-scoped tables | SEC-04, SEC-23 |
| 3.7 | Super Admin `BYPASSRLS` role used outside the `/admin` surface | L | High | Not yet independently audited in code — flagged for SEC-04 to confirm the bypass role is only reachable through admin-scoped dependencies | SEC-04 |

## 4. PII Exposure

Phone, email, and any linked external-provider subject identifier are Tier-3 fields
(hash + encrypt) per `AGENTS.md` §Restaurant Domain Encryption Rules.

| # | Threat | Likelihood | Severity | Mitigation | Ticket |
|---|--------|:---:|:---:|-----------|:---:|
| 4.1 | Plaintext PII recoverable from a DB dump/backup | L | Critical | `encrypt_pii()` — AES-256-GCM, `ENCRYPTION_KEY_V1` asserted to decode to exactly 32 bytes; format is versioned (`v1:...`) so a future key rotation doesn't require a flag-day migration | SEC-09 |
| 4.2 | Ciphertext pattern analysis (same plaintext → same ciphertext, enabling correlation) | L | Medium | Random 96-bit nonce generated per call via `os.urandom` — same phone encrypted twice yields different ciphertext | SEC-09 |
| 4.3 | PII recoverable from application logs | M | High | Structlog event names avoid raw PII fields by convention (e.g. `customer.otp.sent` logs `identifier_type`, never `identifier`); **gap:** no automated log-scrubbing test currently enforces this beyond code review | SEC-09 |
| 4.4 | Account takeover via unverified-email join key (attacker registers victim's email at Firebase/Supabase, inherits victim's local stamps/reward balance) | M | Critical | `_verified_email`/`_verified_phone` in `identity_link_service.py` only treat an identifier as a join key when the provider itself vouches it's verified (`email_verified`, `phone_verified`); an unverified email is written **nowhere searchable** (`email_hash` left null) specifically to prevent this takeover | SEC-08, SEC-09 |
| 4.5 | Provider-subject collision across Firebase/Supabase namespaces | L | High | `subject_hash()` hashes `"<provider>:<sub>"`, not the bare subject — a collision between namespaces cannot cross-authenticate | SEC-08 |
| 4.6 | GPS coordinates stored as raw lat/lng columns (queryable location history) | L | Medium | `AGENTS.md` mandates a PostGIS geometry column only, no separate `latitude`/`longitude` columns; `StampLog` additionally records `gps_latitude_at_scan`/`gps_longitude_at_scan` per-scan for fraud audit — **flagged for SEC-09 to confirm these per-scan fields are treated as Tier-2/3 data**, not left as an unreviewed exception to the "no raw lat/lng" rule | SEC-09 |
| 4.7 | IP address stored raw, enabling deanonymization of anonymous QR scans | M | Medium | `loyalty_service.process_scan` only ever stores `sha256_hex(client_ip)` as the rate-limit identity, never the raw IP | SEC-09 |

## 5. GMB OAuth Interception

**Current status: not yet implemented.** `app/services/gmb_service.py` and
`app/api/v1/routers/reputation.py` are both single-line stubs — no OAuth flow, no PKCE, no
token exchange exists in this codebase yet. This section documents the threat and the
mitigation contract the eventual implementation (REVIEW-03) must satisfy, per Doc 3 §1 Flow 3.

| # | Threat | Likelihood | Severity | Required Mitigation (not yet built) | Ticket |
|---|--------|:---:|:---:|-----------|:---:|
| 5.1 | Authorization code interception (mobile/OS-level code capture) | — (n/a: unbuilt) | Critical | PKCE with `code_verifier = secrets.token_urlsafe(64)`, `code_challenge_method = S256` — an intercepted code is useless without the verifier | SEC-11 |
| 5.2 | CSRF on the OAuth callback (attacker links their own GMB account to the victim's tenant) | — | High | `state` parameter bound to the initiating session, validated on callback | SEC-11 |
| 5.3 | GMB refresh token stored in plaintext | — | Critical | Must follow the same Tier-3 hash+encrypt pattern as other PII once implemented | SEC-09, SEC-11 |
| 5.4 | Open redirect via a manipulated `redirect_uri` | — | Medium | Exact-match allowlist for `redirect_uri`, never a wildcard | SEC-11 |

**Action for SEC-01 sign-off:** this category cannot be marked "mitigated" until REVIEW-03/SEC-11
land. It is included here in full per the ticket's 8-category requirement, with its unbuilt
status stated plainly rather than assessed as a false "planned ✅".

## 6. Payment Webhook Replay (Razorpay)

The ticket's original category name is "Stripe webhook replay"; the codebase implements
Razorpay (`app/core/razorpay_signature.py`, `app/services/billing_service.py`,
`app/api/v1/routers/billing.py`). Assessed against what's actually built.

| # | Threat | Likelihood | Severity | Mitigation | Ticket |
|---|--------|:---:|:---:|-----------|:---:|
| 6.1 | Forged webhook body (no valid signature) moves a tenant to a paid plan for free | M | Critical | `verify_webhook_signature` runs on the **raw, unparsed** request bytes before any JSON decoding or DB access; HMAC-SHA256 keyed by `RAZORPAY_WEBHOOK_SECRET`, compared with `hmac.compare_digest` (constant-time) | SEC-10 |
| 6.2 | Timing attack on the signature comparison | L | High | `hmac.compare_digest`, never `==`, used for both webhook and Checkout-callback signature checks | SEC-10 |
| 6.3 | Replay of a validly-signed webhook (at-least-once redelivery, or an attacker resending a captured legitimate payload) | M | Medium | Idempotent by construction: `BillingEvent.provider_event_id` has a unique DB constraint, and the **INSERT** — not a prior SELECT — is the arbiter, so two concurrent deliveries of the same event can never both apply (`billing_service.handle_webhook` docstring) | SEC-10, SEC-15 |
| 6.4 | Body re-encoding changing the digest and rejecting legitimate webhooks (self-inflicted, but a security-adjacent correctness bug) | L | Low | Deliberately a hand-rolled `bytes -> bool` function rather than the SDK helper, specifically to avoid a decode/re-encode round trip before verification (module docstring) | SEC-10 |
| 6.5 | Checkout-callback signature confused with the webhook signature (two different secrets, same shape) | M | Medium | `is_valid_payment_signature` is keyed by `RAZORPAY_KEY_SECRET`, webhooks by `RAZORPAY_WEBHOOK_SECRET` — documented explicitly in the module docstring as "the single most common cause of 'valid signature rejected'" | SEC-10 |
| 6.6 | Missing signature header silently accepted | L | Critical | `is_valid_signature` returns `False` for any missing signature *or* missing secret — a misconfigured deployment (empty secret) rejects all webhooks rather than accepting all of them | SEC-10 |

## 7. LLM Prompt Injection

**Current status: not yet implemented.** `app/core/prompt_guard.py` is a single-line docstring
stub with no sanitisation logic, and `app/services/ai_engine.py` (the GPT-4o/Gemini
orchestrator) is likewise an empty stub. Neither is imported or called anywhere in the app.
The review composer (REVIEW-01) that would carry customer-supplied "tags" into an LLM prompt
does not yet exist either.

| # | Threat | Likelihood | Severity | Required Mitigation (not yet built) | Ticket |
|---|--------|:---:|:---:|-----------|:---:|
| 7.1 | Direct instruction override ("ignore all previous instructions...") via a review tag | — (n/a: unbuilt) | High | Input sanitisation in `prompt_guard.py` (strip markdown/HTML/control chars per its own docstring) + structural prompt separation (system prompt never concatenated with raw user text) | SEC-12 |
| 7.2 | System-prompt exfiltration via injected fake role markers (`"SYSTEM: ..."`) | — | High | Prompt guard strips/flags role-marker patterns before the tag reaches the model | SEC-12 |
| 7.3 | SQL injection riding along inside a "review tag" field | — | Critical | Pydantic v2 schema validation + SQLAlchemy ORM parameterisation (no f-string SQL per `AGENTS.md` rule) — this layer already exists generally in the codebase even though the AI path doesn't yet | SEC-12, SEC-20 |
| 7.4 | Oversized tag used to pad/hide an injection payload or exhaust token budget | — | Medium | `max_length` Pydantic constraint on tag fields | SEC-12 |
| 7.5 | Injected content causing the AI to draft a response containing secrets or internal instructions | — | High | Output-side check: generated draft scanned for known secret patterns before being surfaced to the owner for approval | SEC-12 |

**Action for SEC-01 sign-off:** flagged unbuilt for the same reason as GMB OAuth — REVIEW-01
and SEC-12 must land together, `prompt_guard.py` must actually be wired into `ai_engine.py`'s
prompt construction, not just exist as a file.

## 8. Loyalty QR Scan Fraud

`app/services/loyalty_service.py` and `app/services/geofence_service.py` implement the
core scan path; `app/core/anti_fraud.py` (impossible-travel / device-fingerprint / rapid-fire
detection) is a stub — its docstring names exactly those checks but contains no code, and
nothing imports it.

| # | Threat | Likelihood | Severity | Mitigation | Ticket |
|---|--------|:---:|:---:|-----------|:---:|
| 8.1 | Client submits a fabricated `is_fraudulent=false` or a spoofed distance in the request body | L | High | Server never trusts client-submitted fraud/distance fields — `distance_to_branch_m` is computed server-side via PostGIS `ST_Distance` on every scan, and `is_fraudulent` is set purely from that server computation (`process_scan`) | SEC-13 |
| 8.2 | GPS spoofing (fake-GPS app reports coordinates inside the geofence from off-site) | H | High | **No mitigation currently implemented.** The geofence check trusts client-reported `gps_lat`/`gps_lng` at face value; only *impossible* values (outside the radius) are caught. Device attestation / impossible-travel checks are the named job of `anti_fraud.py`, which is an empty stub | SEC-13 (gap) |
| 8.3 | Screenshotting a QR code and scanning it from home/elsewhere | M | Medium | Geofence check still applies to the screenshot the same as a live scan — GPS outside radius → `is_fraudulent=True`, no stamp | SEC-13 |
| 8.4 | Rapid repeat scans to farm stamps | M | Medium | Redis rate-limit key `stamp:{branch_id}:{identity}` — one stamp per branch per hour per customer (or per-IP hash when anonymous) | SEC-13 |
| 8.5 | QR token guessing or brute-forcing | L | High | `secrets.token_urlsafe(32)` intended per Doc 3 (≥32 chars, uniqueness enforced) — **`qr_service.py` is currently a stub with no generation code**, so this control is unverified against a real implementation | SEC-14 (gap) |
| 8.6 | Anonymous scan (no `customer`) rate-limited by IP hash only, defeated by IP rotation | H | Low | Accepted risk for the anonymous path — anonymous stamps are explicitly not persistently tracked per Doc 3 §2, so the abuse ceiling is one extra unlinked `StampLog` row, not reward theft | Accepted risk |
| 8.7 | Inactive/deleted branch QR still redeemable | L | Medium | `_get_active_branch` filters on `Branch.is_active.is_(True))`; an inactive branch's QR returns `LOYALTY_INVALID_QR` | SEC-13 |
| 8.8 | Impossible-travel pattern (same customer scans two branches minutes apart, geographically implausible) | M | Medium | **Not implemented** — this is `anti_fraud.py`'s stated purpose and it is currently empty | SEC-13 (gap) |

---

## Summary — Gaps Requiring Follow-Up Before SEC-17 (Pentest Plan)

SEC-17 depends on SEC-01 through SEC-16 being complete. The threats above marked **(gap)** or
"not yet implemented" are the concrete blockers this document surfaces for that dependency
chain — pentesting an unbuilt control produces no signal:

1. **GMB OAuth (§5)** — entirely unbuilt (`gmb_service.py`, `reputation.py` are stubs). Blocks SEC-11.
2. **LLM prompt injection guard (§7)** — `prompt_guard.py` and `ai_engine.py` are stubs, not wired to anything. Blocks SEC-12.
3. **Loyalty anti-fraud beyond geofence (§8.2, §8.8)** — `anti_fraud.py` is a stub; GPS spoofing and impossible-travel have no detection today. Blocks SEC-13.
4. **QR token generation (§8.5)** — `qr_service.py` is a stub; entropy/uniqueness claims in Doc 3 are unverifiable until it exists. Blocks SEC-14.
5. **OTP delivery-failure signalling (§1.7)** — best-effort send failures aren't surfaced to the caller. Minor, but worth a follow-up ticket.
6. **Log PII scrubbing (§4.3)** and **Super Admin BYPASSRLS scope (§3.7)** — currently enforced by convention/code review only, not by an automated check.

## Review Sign-Off

| Reviewer | Role | Date | Verdict |
|----------|------|------|---------|
| _pending_ | Backend Dev | — | Awaiting review — required by SEC-01 acceptance criteria before this document is considered complete |

---

*Document owner: Security Dev · Source: SEC-01 · Last updated against branch `feature/auth-04-rbac-roles`.*
