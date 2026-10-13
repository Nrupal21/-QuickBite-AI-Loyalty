# Security Findings Report

**Ticket:** SEC-24 · **Status:** DRAFT (Revision 1, 2026-10-13) - **not final, not distributed**
**Owner:** Security Dev · **Scope:** findings from SEC-18 through SEC-23

> **Read this first.** SEC-24 depends on SEC-18 to SEC-23 being *executed*. Only
> three of the six have been (the automated ones). This report therefore
> documents every finding that exists today and states plainly what has not been
> tested. It must not be read as "no other findings exist" - see
> [Execution coverage](#execution-coverage).

## Executive summary

| Severity | Total | Open | Fixed in working tree (uncommitted) | Fixed, verification pending |
|----------|-------|------|--------------------------------------|------------------------------|
| Critical | 1 | 0 | 0 | 1 |
| High | 0 | 0 | 0 | 0 |
| Medium | 5 | 3 | 2 | 0 |
| Low | 6 | 4 | 2 | 0 |
| **Total** | **12** | **7** | **4** | **1** |

- **Highest-risk item still open:** F-02 (Super Admin `BYPASSRLS` role grants never
  audited) and F-01 (client-IP resolution behind a proxy - unverified, could
  collapse every rate limit onto one key).
- **The only Critical** (F-10, row-level security not enforced for the table owner)
  was fixed by migration `0006`; whether it holds in a real deployment is exactly
  what SEC-23 verifies, and SEC-23 has not run.
- **Fix tickets:** no High findings exist; the one Critical is already fixed in code.
  Proposed tickets for the open Medium items are in [Follow-ups](#follow-ups).
  None have been created in a tracker.

## Execution coverage

| Ticket | Activity | Executed? | Evidence |
|--------|----------|-----------|----------|
| SEC-18 | Auth bypass | **Yes** (automated) | `tests/security/test_sec18_auth_bypass.py` |
| SEC-19 | OTP attacks | **Yes** (automated; timing test 2.3 is a documented skip) | `tests/security/test_sec19_otp_attacks.py` |
| SEC-20 | Injection | **Yes** (automated) | `tests/security/test_sec20_injection_attacks.py` |
| SEC-21 | OWASP ZAP scan | **NO** - needs staging | - |
| SEC-22 | Burp Suite manual pass | **NO** - needs staging | - |
| SEC-23 | RLS cross-tenant, staging | **NO** - no staging; tooling ready | `scripts/verify_rls_isolation.py`, `tests/security/test_sec23_rls_isolation.py` |

`pytest tests/security` on 2026-10-13: **44 passed**. Passing automated tests
show the *mechanism* works at unit level with mocked DB/Redis; they do not replace
the over-the-wire passes SEC-21/22 exist for.

---

## Findings (sorted by severity)

### F-10 - Row-level security was inert for the table owner · **CRITICAL** · Fixed, verification pending

- **Evidence:** the docstring of migration `0006_force_row_level_security.py`
  records that policies from `0001`/`0002` "have never actually filtered anything":
  no table used `FORCE ROW LEVEL SECURITY`, so any owning connection saw every
  tenant's rows. Demonstrated there: with tenant A bound, a count over
  `restaurant.audit_logs` returned both tenants' rows.
- **Affected:** all tenant-scoped tables (cross-tenant read of every tenant's data).
- **Remediation applied:** `0006` forces RLS; `scripts/create_app_role.py` provisions a
  `NOSUPERUSER NOBYPASSRLS` app role. **Both halves are required** - a superuser or
  `BYPASSRLS` connection is exempt regardless of FORCE.
- **Still to prove:** that the *deployed* app connects as `quickbite_app`, and that
  all RLS tables (now more than the ticket's "15" - 16 in `0006` plus later
  additions) isolate correctly with real data. `verify_rls_isolation.py` aborts if
  the connection role is superuser/`BYPASSRLS`. **Closes only when SEC-23 runs.**

### F-01 - Client IP resolution behind a proxy is unconfigured · **MEDIUM** · Open, needs staging verification

- **Evidence:** every IP-keyed control reads `request.client.host`
  (`get_remote_address` in `app/core/rate_limiter.py:37`, `routers/loyalty.py:68`,
  `routers/customers.py:31`, `routers/auth.py:72`). The `Dockerfile` runs
  `gunicorn ... uvicorn.workers.UvicornWorker` with no `--forwarded-allow-ips`, and
  no `ProxyHeaders` handling exists in `app/`. Behind a load balancer/CDN, the
  server would see the proxy's address for every client.
- **Impact if confirmed:** (a) all SlowAPI limits (e.g. 20/min on OTP endpoints) become
  one global bucket - self-inflicted denial of service for real diners; (b) anonymous
  scans key on `sha256(client_ip)` (`loyalty_service.py`), so **all anonymous
  customers share one stamp slot per branch per hour**; (c) session/IP hashes
  lose meaning for forensics. Conversely, if the platform forwards headers and the
  app trusts them too broadly, clients could spoof their IP.
- **Not verified:** the deployment platform is not defined in this repo. Check
  `request.client.host` on staging behind the real ingress.
- **Remediation:** set `FORWARDED_ALLOW_IPS` (gunicorn) to the ingress's addresses only;
  add a staging test that two different client IPs get separate rate-limit buckets.

### F-02 - Super Admin `BYPASSRLS` role grants never audited · **MEDIUM** · Open

- **Evidence:** `quickbite_admin_bypass` is `NOLOGIN BYPASSRLS`, activated via
  `SET LOCAL ROLE` (`app/db/rls.py`). Its grants are scattered across migrations
  `0010`, `0015`, `0016`, `0017`, `0018`; `THREAT_MODEL.md` §3.7 flags the scope as
  enforced "by convention/code review only". Pentest plan 6.3 calls it the single
  most important open item.
- **Affected:** any code path that enters admin-bypass context (`admin_service.py`,
  `auth_service.py`, `campaign_service.py`, `user_oauth_service.py`,
  `user_otp_service.py`).
- **Impact:** a bug or injection reaching `SET LOCAL ROLE` would read/write across all
  tenants within whatever the role is granted - a larger blast radius than intended
  if grants have crept.
- **Remediation:** on staging, dump `information_schema.role_table_grants` for the role
  and diff against the five migrations; add that diff as an automated test.

### F-03 - Loyalty anti-fraud: GPS spoofing and impossible travel undetected · **MEDIUM** · Open (already tracked)

- **Evidence:** `app/core/anti_fraud.py` is a 5-line stub; `THREAT_MODEL.md` §8.1, §8.2,
  §8.8. The geofence trusts client-supplied coordinates with no independent signal.
- **Impact:** a diner who holds a QR token can collect stamps remotely. Bounded by
  one stamp per customer per hour per branch (see F-06), so loss is limited but real.
- **Remediation:** IP-geolocation cross-check and velocity (impossible-travel) rule in
  `anti_fraud.py`, flagging `is_fraudulent`. Track under the existing SEC-13 gap.

### F-06 - Stamp rate limit was a check-then-set race · **MEDIUM** · Fixed in working tree (uncommitted)

- **Evidence:** `loyalty_service.process_scan` checked Redis key `stamp:{branch}:{id}` then
  set it only *after* the DB commit, so concurrent requests all passed the check.
  Found by code reading while reviewing pentest plan 8.4.
- **Impact:** duplicate stamps / earlier-than-allowed rewards via a double-tap or script.
- **Remediation applied:** atomic `set_if_absent` claim before any write, released if the
  commit fails. `tests/unit/test_loyalty_scan.py::test_concurrent_scans_for_one_customer_award_exactly_one_stamp`
  (10 interleaved scans -> 1 stamp).
- **Still to prove:** live staging run (the unit test mocks Redis).

### F-07 - GMB OAuth lacked PKCE · **MEDIUM** · Fixed in working tree (uncommitted)

- **Evidence:** SEC-11. The flow used only a `secrets.token_urlsafe(32)` CSRF state; no
  `code_challenge`/`code_verifier`.
- **Impact:** an intercepted authorization code could be redeemed by an attacker.
- **Remediation applied:** S256 PKCE; verifier (`token_urlsafe(64)`) held server-side in
  the single-use Redis state, never sent to the browser; pre-PKCE states redirect to
  `?gmb=expired`. Tests in `tests/unit/test_gmb_oauth.py`. Needs Security Dev review.

### F-04 - OTP request cost amplification via identifier cycling · **LOW** · Open (corrects pentest plan 2.5)

- **Evidence:** `POST /auth/customer/otp-request` has a per-client-IP limit of
  20/min (`routers/customer_auth.py:28`), plus per-identifier limits in
  `customer_otp_service.py`. **The first draft of the pentest plan (item 2.5) said no
  per-IP throttle existed; that was incorrect and has been corrected.** The route path
  in that row (`/otp/request`) was also wrong.
- **Residual risk:** 20/min/IP is still ~28,800 SMS/day from one IP with rotating
  numbers, and the limit is only as good as F-01.
- **Remediation:** a global/daily SMS budget per tenant and CAPTCHA/step-up after N distinct
  identifiers per IP.

### F-05 - No SSRF allowlist · **LOW** · Open (no current sink)

- **Evidence:** no private-IP/link-local guard or outbound-URL allowlist in `app/`. A
  regression tripwire (`test_no_schema_field_accepts_an_arbitrary_server_fetched_url`)
  fails the build if a user-controllable URL field is added. Today's outbound calls
  (Google, Razorpay, Meta) use fixed hostnames.
- **Impact:** none today; becomes High the moment a user-supplied URL is fetched.
- **Remediation:** an `httpx` wrapper that resolves and rechecks the destination at
  request time (DNS-rebinding-safe) before any such feature ships.

### F-08 - QR tokens were 128-bit; legacy tokens remain · **LOW** · Fixed for new tokens (uncommitted)

- **Evidence:** SEC-14. `generate_qr_token()` used `token_urlsafe(16)` (22 chars); spec is
  `token_urlsafe(32)`. Now in `qr_service.py` at 32 bytes (43 chars).
- **Residual:** branches created earlier keep 22-char tokens until regenerated. Not a
  practical brute-force risk (128-bit) but fails the ticket's `length >= 32` check.
- **Remediation:** staging query `SELECT length(qr_code_token), count(*) FROM
  restaurant.branches GROUP BY 1`; rotate short ones deliberately - rotation invalidates
  printed receipts, so it needs the owner's consent.

### F-09 - Non-idempotent background sends · **LOW** · Fixed in working tree (uncommitted)

- **Evidence:** SEC-15. (a) The reward-unlocked WhatsApp had no dedup, so a repeated
  call re-sent it. (b) The GMB reply set its Redis flag after the DB commit, so a worker
  crash between the Google call and the commit caused a second call on retry.
- **Remediation applied:** atomic claim keyed on reward code (released on failure); GMB
  flag set before the commit with the retry repairing row state. Tests in
  `test_loyalty_scan.py` and `test_response_service.py`.
- **Residual:** a hard crash between claiming and sending suppresses the WhatsApp until
  the 24h key expires - chosen deliberately (a duplicate alert is worse than a missed one).

### F-11 - Log PII scrubbing enforced by convention only · **LOW** · Open (already tracked)

- **Evidence:** `THREAT_MODEL.md` §4.3; no automated check that logs omit phone/email.
- **Remediation:** a structlog processor that redacts known PII keys, plus a test.

### F-12 - OTP delivery failures not surfaced to the caller · **LOW** · Open (already tracked)

- **Evidence:** `THREAT_MODEL.md` §1.7. Sends are best-effort.
- **Impact:** a diner may wait for a code that was never sent; no enumeration impact.

---

## Follow-ups

Proposed fix tickets (**not created** - no tracker access in this session):

1. Trusted-proxy / client-IP configuration + staging test (F-01).
2. Audit and test `quickbite_admin_bypass` grants (F-02).
3. Implement `anti_fraud.py` velocity + geo checks (F-03).
4. Per-tenant SMS budget / CAPTCHA step-up (F-04).
5. Rotate legacy 22-char QR tokens with owner consent (F-08).
6. Run SEC-21, SEC-22, SEC-23 on staging, then **re-issue this report** (any new findings).

## SEC-24 acceptance status

| # | Criterion | Status |
|---|-----------|--------|
| 1 | All findings from SEC-18 to SEC-23 documented | **Not met** - SEC-21/22/23 not executed |
| 2 | Each finding: ID, title, severity, evidence, endpoint, remediation | Met for the findings above (endpoint given where one applies) |
| 3 | Sorted by severity, Critical first | Met |
| 4 | Executive summary with counts per severity | Met |
| 5 | Fix tickets created for all Critical + High | **Not done** - 0 High; Critical already fixed in code; no tracker access |
| 6 | Report distributed to all 7 team members | **Not done** - requires a human; draft only |

## Review

| Reviewer | Role | Date | Verdict |
|----------|------|------|---------|
| _pending_ | Backend Dev | | |
