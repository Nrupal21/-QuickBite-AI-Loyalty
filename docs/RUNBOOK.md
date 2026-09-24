# Incident Response Runbook

**Ticket:** SEC-27 · **Status:** Draft — procedures written, drills NOT yet run
**Owners:** Security Dev (author), DevOps + 1 Backend Dev (review pending)

> **Read this before you rely on it.** This runbook documents the procedures
> *as the code supports them today*. Per SEC-27's own acceptance criteria, it
> is not complete until:
> - `SECRET_KEY` rotation has been tested in staging (zero downtime).
> - Force-logout-all has been tested in staging.
> - A DB PITR restore has been tested on a clone (restore to 1h ago).
> - This document has been reviewed by DevOps + 1 Backend Dev.
>
> None of those four have happened yet (see SEC-30 for the drill tracking
> ticket). Treat every "Verify" step below as *unverified in practice* until
> a drill confirms it — update this file's status line and add a dated entry
> to the Drill Log at the bottom each time one does.

## How to use this document

Each scenario follows the same shape: **Detect → Contain → Investigate →
Remediate → Communicate → Post-incident**. Redis keys, table names, and code
paths below are cited so the on-call engineer isn't guessing during an
incident — if a cited path has moved, fix the citation as part of triage, not
after.

Current detection is **manual** — no `SEC-29` alerting exists yet (no Sentry
`sentry_sdk.init()` call, no Prometheus, no Grafana are wired up in this
codebase as of this writing). Until SEC-29 ships, "Detect" steps below mean
*someone noticed* — a support ticket, a manual log/DB query, or a Razorpay
dashboard alert — not an automated page. Where useful, we note the SEC-29
target-state alert this section should eventually escalate on.

---

## 1. Compromised JWT (owner/staff access token)

**Scenario:** A single owner/staff access token has leaked (found in a public
repo, a log dump, a phishing report) but the signing key itself is not
suspected to be compromised. If the *key* is suspected compromised, skip to
Scenario 2 instead — revoking one token does nothing for a stolen key.

**Detect**
- Report from the affected user, a leaked-credential scanner hit, or unusual
  activity on the account (audit log entries — `AuditLog` rows via
  `app/db/models/audit.py` — from a new IP/location for that user).
- Target state (SEC-29, not yet built): Sentry alert on >50 auth-related 401s/
  minute from a single `sub`, or a spike in `jwt_revocations`.

**Contain — revoke the specific token (seconds)**
1. Identify the token's `jti` claim (decode it, or pull from
   `request.state.jwt_claims` in recent logs if still captured).
2. Add it to the Redis single-session revocation blocklist:
   `revoked_jti:{jti}` — this is exactly what `/auth/logout` does
   (`auth_service.py`), and `_resolve_local()` in
   `app/api/v1/dependencies/auth.py:192-194` checks this key on every request.
   TTL it to the token's remaining `exp` — no need to keep it past that.
3. This blocks *only that token*. Its refresh token (if any) is unaffected —
   proceed to step 4 unless you're confident the attacker never had it.

**Contain — revoke every session for that user (if in doubt, do this instead)**
4. Call `AuthService.logout_all()` for the user — either via the user's own
   "log out everywhere" action, or, for an unresponsive/compromised account,
   as **Super Admin** via `AdminService.force_logout_user()`
   (`app/services/admin_service.py:175-219`). This bumps the user's
   `tokens_valid_from` watermark; every token issued with an `iat` before that
   watermark is rejected by `_assert_not_globally_revoked()`
   (`app/api/v1/dependencies/auth.py:157-177`), including any token whose
   `jti` you don't know about. This is the *only* revocation path that also
   reaches externally-issued Supabase/Firebase sessions for that user, since
   neither provider exposes a server-side per-session revoke.
5. `force_logout_user()` runs under `rls.admin_bypass_context()` and writes an
   `admin.force_logout` `AuditLog` row — confirm that row exists as evidence
   the action was taken and by whom.

**Investigate**
- Pull `AuditLog` rows for the user's `tenant_id` around the suspected leak
  window — look for `login_success` from an unfamiliar `ip_address_hash`
  (hashed, so you're matching a hash of a known bad IP, not reading it raw).
- Check whether the account has MFA enabled (`user.mfa_enabled`). If not, and
  the role requires it (`role.mfa_required`), that's a contributing gap —
  file a follow-up, don't silently let it slide.
- Determine how the token leaked (support chat log, a browser extension, a
  public paste) — this changes whether contact with the user is also needed.

**Remediate**
- Confirm the user re-authenticates cleanly after the watermark bump (their
  old token must now 401; a fresh login must succeed).
- If the leak came from a systemic source (e.g., tokens logged in an error
  tracker before PII scrubbing), that's a separate incident — treat the
  logging path as the root cause and open a ticket against it.

**Communicate**
- Notify the affected user that their session was force-logged-out and why.
- If tenant data may have been accessed with the token, notify the tenant
  owner per the tenant's data-access agreement.

**Post-incident**
- Add a dated entry below once this has actually been drilled.

---

## 2. `SECRET_KEY` rotation (owner/staff signing key compromised)

**Scenario:** The HS256 signing key used for all owner/staff tokens
(`settings.SECRET_KEY`, `app/core/security.py`) is suspected compromised —
found in a leaked `.env`, a misconfigured log, or a compromised secrets-manager
credential. This is blunter and more disruptive than Scenario 1: every owner/
staff session everywhere is about to be invalidated at once.

**Known limitation — read before you start:** token verification in
`app/core/security.py::decode_access_token()` uses a single, current
`SECRET_KEY` with no key-ID (`kid`) claim and no dual-key verification window.
There is **no graceful rotation** today — the moment the new key is live, every
token signed with the old key fails to decode and every owner/staff user is
logged out simultaneously. SEC-27's "zero downtime" acceptance criterion means
*the API stays up*, not that sessions survive — sessions do not survive this
procedure as currently built. If session continuity through rotation is
required, that's a code change (add `kid` + a short-lived key-verification
list), not an ops procedure — raise it as a follow-up ticket rather than
assuming this runbook can paper over it.

**Contain**
1. Generate a new `SECRET_KEY` (≥ 64 hex chars, matching `.env.example`'s
   requirement) and confirm it is still different from `CUSTOMER_SECRET_KEY` —
   `config.py`'s `validate_separate_jwt_keys` will refuse to start otherwise
   (`app/core/config.py:403-409`), which is a useful pre-deploy safety check,
   not just an incident-time one.
2. Update the secret in your secrets manager (AWS Secrets Manager per SEC-30's
   drill description) and redeploy every API instance so all of them pick up
   the new value — a rolling deploy that leaves old and new instances live
   simultaneously will intermittently reject valid tokens signed moments
   apart; prefer the fastest full-fleet rollout your deploy tooling supports.
3. This step alone does **not** revoke refresh tokens, which are opaque
   `secrets.token_urlsafe(48)` values hashed and stored on `Session` rows
   (`security.py:94-96`), unrelated to `SECRET_KEY`. If the compromise could
   include the database (not just the key), also revoke refresh tokens — see
   step 4.
4. If there's any chance the attacker also has valid refresh tokens (e.g. DB
   access, not just the key), treat this as Scenario 6 (DB restore/compromise)
   as well — a new signing key does not help against a stolen refresh token,
   since refresh rotation issues a *fresh* access token signed with whatever
   key is currently configured.

**Investigate**
- Determine the blast radius: was this key ever logged, committed, or exposed
  in a build artifact? `git log -p -- .env* | grep -i secret` (on a clone, not
  the live repo) and check CI/CD build logs.
- Check `.env` is still `.gitignore`d (`AGENTS.md` §"What NOT to Do" — never
  commit secrets) and that git history doesn't already contain it.

**Remediate**
- After rotation, spot-check that a fresh login issues a token verifiable
  with the new key, and that a pre-rotation token now 401s
  (`decode_access_token()` will raise `InvalidSignatureError`, converted to
  the generic `_UNAUTHORIZED` in `dependencies/auth.py`).
- Force every owner/staff user to re-authenticate (this happens automatically
  as a side effect — there is nothing more to "force").

**Communicate**
- This affects every restaurant owner and staff member across every tenant —
  a proactive status-page notice ("please log in again") is warranted, not
  just silence followed by support tickets.

**Post-incident**
- File a ticket to add `kid`-based key rotation if this incident (or the
  SEC-30 drill) shows the blast radius of "every session everywhere,
  simultaneously" is unacceptable.

---

## 3. RLS (row-level security) bypass / suspected cross-tenant data leak

**Scenario:** A report or anomaly suggests Tenant A can see Tenant B's data —
a customer's own restaurant's reviews mixed with another's, an API response
with foreign `tenant_id` rows, or anything similar.

**Detect**
- Customer/owner report of seeing unfamiliar data.
- Anomalous `admin_bypass` usage — Super Admin cross-tenant reads run under
  `rls.admin_bypass_context()` (`app/db/rls.py:108-124`, `SET LOCAL ROLE
  quickbite_admin_bypass`), which is legitimate for `/admin` but should
  correlate 1:1 with an `AuditLog` entry naming a Super Admin actor. A
  bypass-role read with no corresponding audit entry is the signature of this
  incident, not routine admin use.

**Contain**
1. If you can identify the specific endpoint/query, and the risk is severe,
   consider a feature-flag/route disable rather than a full outage. There's no
   existing kill-switch mechanism in this codebase for this — a full rollback
   to a known-good deploy is the fallback if no such flag exists yet.
2. Do **not** run `SET row_security = off` or otherwise widen the isolation
   posture "to see what's happening" — that is explicitly forbidden
   (`AGENTS.md` §3 "RLS Enforcement") and would broaden the exposure you're
   trying to contain.

**Investigate — in this order**
1. **Which policy should have applied?** Every RLS-protected table is enabled
   via `_enable_rls()` (`app/db/migrations/versions/0002_four_domain_schemas.py:26-31`):
   `CREATE POLICY tenant_isolation_{table} ... USING (tenant_id =
   current_setting('app.tenant_id')::uuid [OR tenant_id IS NULL])`. Confirm
   the affected table actually has this policy:
   `SELECT * FROM pg_policies WHERE tablename = '<table>';` — a table added
   after migration `0002` without its own `_enable_rls()` call is a plausible
   root cause.
2. **Was FORCE ROW LEVEL SECURITY applied?** Migration `0006_force_row_level_security.py`
   closes the table-owner bypass hole (`ENABLE` alone doesn't restrict the
   table owner; `FORCE` does). Confirm: `SELECT relforcerowsecurity FROM
   pg_class WHERE relname = '<table>';` should be `true`. A connection using
   the table-owning role bypasses `ENABLE`-only RLS entirely, so this check
   matters as much as the policy itself.
3. **Was tenant context actually bound before the query?** `rls.py`'s
   `after_begin` listener (`_reapply_tenant_context`, L92-105) re-applies
   `app.tenant_id` on every new transaction because `SET LOCAL` is wiped by
   `COMMIT` — a code path that opens a new transaction without going through
   the normal session lifecycle (a Celery task using a raw connection, a
   manually-managed transaction) could slip through uncontexted. Check
   `app/tasks/` and `app/services/billing_service.py::handle_webhook` (which
   explicitly calls `rls.tenant_context()` since it has no request-scoped
   session, per `app/db/rls.py:76-89`) for the specific code path involved.
4. **Was the `quickbite_admin_bypass` role scope wider than intended?**
   Per `docs/THREAT_MODEL.md` §3.7, this role's exact table/schema grants have
   **not been independently audited in code** as of this writing. If the
   affected surface is anywhere near an admin-panel query, check the role's
   actual `GRANT`s (`\dp` in psql, or query `information_schema.role_table_grants`
   for `quickbite_admin_bypass`) against what `admin_service.py` actually
   needs — this is the known open gap SEC-01 already flagged, not a new one.
5. **Was the leak via the JWT `tenant_id` claim, not the DB row?** `_resolve_local()`
   binds RLS context from the *claim* before the user row is loaded, purely to
   make the row visible under RLS — but authorization is required to still
   come from `user.tenant_id` on the loaded row (`Principal`'s invariant,
   `app/core/principal.py:48-55`). If a code path was found trusting
   `claims.get("tenant_id")` for anything beyond that initial narrowing scope,
   that is the bug — not RLS itself.

**Remediate**
- Whatever the root cause (missing policy, missing FORCE, uncontexted
  transaction, over-broad bypass role, or a code path trusting the claim),
  fix at that layer and add a regression test asserting 0 cross-tenant rows
  for the specific table/path involved (see `docs/PENTEST_PLAN.md` §3 and
  SEC-23's per-table verification approach for the pattern).
- Re-run `pg_policies`/`relforcerowsecurity` checks across **all** 15
  RLS-protected tables, not just the one implicated — a missing `FORCE` on one
  table suggests it's worth checking the others were not similarly missed.

**Communicate**
- Cross-tenant data exposure is a reportable incident for affected tenants
  under most data-processing agreements — loop in whoever owns compliance
  before tenant notification, but don't let that gate containment.

**Post-incident**
- If the root cause was the unaudited `quickbite_admin_bypass` scope, that
  closes part of the SEC-01/SEC-17 gap list — update `docs/THREAT_MODEL.md`
  §3.7 to reflect the audit once done, don't leave it marked as still-open.

---

## 4. Razorpay webhook replay / payment tampering

**Scenario:** Suspected replay of a Razorpay webhook event (e.g., a
`subscription.charged` event resent to grant service without a real charge),
or a tampered webhook payload.

**Detect**
- Razorpay dashboard shows a webhook delivery anomaly (repeated deliveries,
  unexpected retry pattern).
- A `BillingEvent` insert failing with `IntegrityError` on
  `provider_event_id`'s unique constraint is *expected, correct behavior*
  (see below) — don't treat that alone as an incident. Investigate only if a
  subscription's entitlement changed *without* a matching real Razorpay event
  in their dashboard.

**How this is protected today (know this before "fixing" it)**
- Signature verification: `app/core/razorpay_signature.py::verify_webhook_signature()`
  (L58-74) computes `hmac.new(RAZORPAY_WEBHOOK_SECRET, raw_body,
  sha256).hexdigest()` and compares with `hmac.compare_digest` (constant-time)
  against the `X-Razorpay-Signature` header, on the **raw, unparsed** request
  body. This is a *different* secret from `is_valid_payment_signature()`
  (L77-95), which validates the Checkout callback using `RAZORPAY_KEY_SECRET`
  — mixing these two up is documented in that file as the most common
  confusion bug; check which one is actually configured if verification is
  unexpectedly failing or passing.
- Replay/idempotency: `billing_service.py::handle_webhook()` (L264-333) writes
  `provider_event_id` (from `X-Razorpay-Event-Id`, or `sha256(raw_body)` if
  that header is absent) into `BillingEvent`, which has a **unique DB
  constraint**. The insert itself is the idempotency check — a duplicate
  raises `IntegrityError`, is caught, rolled back, and returns
  `{"status": "duplicate"}` without reapplying the event (L308-318).
- Stale/out-of-order events: `_apply_subscription_event()` (L381-392) compares
  `provider_event_at` against the subscription's stored value and drops an
  older retried event even if its `provider_event_id` were somehow new.
- Tenant scoping: the handler resolves `tenant_id` from Razorpay's echoed
  `notes` field and binds RLS via `rls.set_tenant_context()` **before** the
  `BillingEvent` insert (L286-298), so a webhook claiming the wrong tenant
  fails the RLS `WITH CHECK`, not silently applies to the wrong tenant.

**Contain**
1. If `RAZORPAY_WEBHOOK_SECRET` itself is suspected compromised (not just a
   replayed legitimate event), rotate it in the Razorpay dashboard and update
   `settings.RAZORPAY_WEBHOOK_SECRET`, then redeploy. Old, already-processed
   events are unaffected (they're already recorded); this only stops new
   forged deliveries.
2. If a specific subscription's entitlement was wrongly granted, manually
   correct it (downgrade/revoke) and record the correction as its own
   `BillingEvent`-adjacent audit trail — don't just edit the row silently.

**Investigate**
- Pull the `BillingEvent` row(s) for the affected `provider_event_id` and
  compare `provider_event_at`/payload against what Razorpay's dashboard shows
  for that event ID — a mismatch means the payload was tampered with
  somewhere in transit or the signature check has a bug, not that replay
  protection failed (replay protection and payload-integrity are separate
  properties — confirm which one actually broke).
- Check webhook delivery logs (Razorpay dashboard → Webhooks → delivery
  attempts) for source IPs outside Razorpay's published webhook IP ranges.

**Remediate**
- If verification passed for a genuinely forged payload, that's a Critical
  finding against `razorpay_signature.py` — stop processing webhooks (feature
  flag or route-level disable) until fixed and re-verified against the
  specific payload that got through.

**Communicate**
- Any incorrect entitlement grant/revocation affecting a paying tenant needs
  direct owner communication, especially if it affected their billing.

**Post-incident**
- Add the specific bypass payload (redacted of the real secret) as a
  regression test in `tests/security/test_sec20_injection_attacks.py` or
  `tests/unit/test_billing_webhook.py`, whichever matches its category.

---

## 5. OTP abuse (mass request / brute-force / SMS-spend attack)

**Scenario:** A spike in OTP requests — either a brute-force attempt against
one identifier, or a broad campaign hitting many phone numbers/emails to
exhaust SMS/email budget or harass users (a classic "OTP bombing" attack).

**Detect**
- Twilio/2Factor.in/SendGrid dashboard shows a cost or volume spike.
- Manual Redis inspection: a burst of `otp_daily:{tenant_id}:{identifier_hash}`
  keys hitting their cap, or a concentration of `otp_cooldown:*` keys being
  set in a short window.
- Target state (SEC-29, not yet built): Prometheus alert on OTP failed-rate
  > 100/min; Grafana `otp_delivery_failure_rate` dashboard.

**How this is protected today**
- Generation: CSPRNG via `secrets.choice(string.digits)` × 6
  (`app/core/security.py::generate_otp_code()`, L118-124) — not
  `random.randint`, per `AGENTS.md` §3.
- Storage: only the SHA-256 hash is ever stored, at
  `otp:{tenant_id}:{identifier_hash}`, TTL `CUSTOMER_OTP_TTL_SECONDS = 300`.
- Rate limits: a 120s cooldown key (`otp_cooldown:{tenant_id}:{identifier_hash}`)
  and a daily cap of `CUSTOMER_OTP_DAILY_MAX = 5` via an atomic Redis `INCR`
  on `otp_daily:{tenant_id}:{identifier_hash}` (→ 429 `OTP_DAILY_LIMIT`).
- Brute-force on verify: 3 wrong attempts
  (`CUSTOMER_OTP_MAX_ATTEMPTS`) deletes the OTP key and forces a fresh
  request (401 `OTP_TOO_MANY_ATTEMPTS`) — a locked-out attacker cannot keep
  guessing against the same code.
- Enumeration: an unknown identifier gets a `200 OTPNewUserResponse`
  (registration flow), never a 404 — see `AGENTS.md` §3 "OTP Enumeration
  Prevention".

**Contain**
1. **Per-identifier abuse** (one number/email being brute-forced): the
   existing 3-attempt lockout already contains this automatically — confirm
   it fired (`customer.otp_attempts` reset to 0, key deleted) rather than
   taking manual action.
2. **Broad campaign abuse** (many identifiers, cost/harassment attack): the
   per-identifier daily cap doesn't stop an attacker cycling through many
   *different* numbers. There is no IP-level or global rate limit visible in
   `customer_otp_service.py` today — the fastest containment is at the edge
   (Cloudflare/WAF rate-limiting or IP block on the `/otp/request` route) or a
   temporary reduction of `CUSTOMER_OTP_DAILY_MAX` / tightening of
   `CUSTOMER_OTP_RATE_LIMIT_SECONDS` via config + redeploy. Treat "no
   IP-level limit on this endpoint" as a gap to ticket, not something this
   runbook can fix by itself.
3. If the abuse targets a single tenant disproportionately (e.g. a
   competitor harassing one restaurant's customers), consider a
   tenant-specific temporary block at the edge while investigating.

**Investigate**
- Identify whether this is one tenant or platform-wide, and whether
  identifiers being targeted look sequential/enumerated (suggests a scraped
  phone-number list) vs. random (suggests a generic bot).
- Check SMS/email provider logs for the actual delivery attempts and their
  cost impact.

**Remediate**
- If containment required an edge rule, ensure it's scoped narrowly enough
  to expire or be removed once the abuse stops — a permanent aggressive
  WAF rule can lock out legitimate customers.
- If this incident reveals the missing IP/global rate limit is a real gap
  (likely), file it as a follow-up ticket against `customer_otp_service.py`
  rather than treating the edge-level mitigation as the permanent fix.

**Communicate**
- If a specific tenant's customers were harassed with unwanted OTP SMS, notify
  that tenant owner.

**Post-incident**
- Record cost impact (SMS spend) if the abuse was volumetric — useful both
  for the incident record and for justifying the rate-limit follow-up ticket.

---

## 6. Database restore (data loss, corruption, or destructive incident)

**Scenario:** Data loss or corruption requiring restoration to a prior point
in time — a bad migration, an accidental bulk delete, ransomware, or
corruption discovered after the fact.

**Known gap — read before you need this:** there is **no backup/restore
tooling in this repository**. `scripts/` contains only seed scripts
(`seed_roles.py`, `seed_plans.py`, etc.) — no backup, snapshot, or restore
script exists. This procedure relies entirely on **Supabase's built-in
Point-in-Time Recovery (PITR)**, since Postgres 16 is hosted on Supabase per
`AGENTS.md` §1. SEC-27 and SEC-30 both require this to be *tested on a clone*
before launch, with a **< 30 minute SLA for a 1-hour-ago restore** (SEC-30) —
that drill has not happened yet, so treat every timing estimate below as
unverified.

**Detect**
- Data-integrity report from a tenant, an alert from a scheduled data-quality
  check (none currently exists — this is itself a gap), or discovery during
  routine operations.

**Contain**
1. **Stop the bleeding first.** If the cause is an in-progress bad process
   (a runaway migration, a buggy bulk job), stop it before restoring —
   restoring while the same process is still running just corrupts the
   restored data again.
2. Determine the exact time to restore to. Supabase PITR restores to a
   specific timestamp, not "the last good state" — you need to know or bound
   that timestamp from logs, audit trails, or user reports before starting.

**Investigate / Plan the restore**
1. In the Supabase dashboard, open the project's **Database → Backups → Point
   in Time Recovery** panel and confirm PITR is enabled and its retention
   window covers the needed timestamp (retention is plan-dependent — confirm
   the current plan's window before assuming any specific timestamp is
   reachable).
2. **Always restore to a new branch/clone first, never in place** — Supabase
   supports restoring PITR into a new project/branch. Validate the restored
   data there before any cutover decision. This is also exactly what the
   SEC-27/SEC-30 "restore on a clone" requirement means — this is not an
   extra step, it's the tested procedure.
3. On the clone, verify:
   - Row counts for the affected table(s) look right for that timestamp.
   - **RLS policies and `FORCE ROW LEVEL SECURITY` survived the restore** —
     a PITR restore should carry schema-level settings, but confirm with the
     same `pg_policies` / `relforcerowsecurity` checks used in Scenario 3
     before trusting the clone with real traffic. Don't assume; check.
   - Alembic's migration version stamp on the clone matches what's expected —
     if the restore point predates a completed migration, you may need to
     re-run migrations forward on the clone before it's usable.

**Remediate — cutover**
1. Once validated, follow Supabase's project-restore/promote flow to make the
   validated clone the new production database, or replay the necessary data
   corrections from it into production if a full cutover isn't appropriate
   for the scope of loss.
2. Confirm the application reconnects cleanly (`DATABASE_URL` update, if the
   connection string changed) and run a smoke test against a low-risk
   read/write path before declaring the incident resolved.

**Communicate**
- Any data loss window needs to be communicated to affected tenants with the
  specific time range affected, once known — "we restored to 2:14 PM, so
  anything written between 2:14 PM and now needs to be re-entered" is the
  kind of concrete detail owners need, not a vague apology.

**Post-incident**
- This is the highest-priority scenario to actually drill (SEC-30, Drill 3)
  before launch, precisely because it has zero in-repo tooling backing it up
  today — everything above is a plan, not a verified procedure.

---

## Drill Log

*(SEC-30: 3 production incident-response drills, off-peak hours. Append one
row per drill — do not delete prior entries.)*

| Date | Drill | Result | Duration | Issues found | Runbook updated? |
|------|-------|--------|----------|---------------|-------------------|
| — | *(none run yet)* | — | — | — | — |

## Review Sign-off

*(SEC-27 requires review by DevOps + 1 Backend Dev before this is considered
complete — not yet obtained.)*

| Reviewer | Role | Date | Signature |
|----------|------|------|-----------|
| _pending_ | DevOps | | |
| _pending_ | Backend Dev | | |
