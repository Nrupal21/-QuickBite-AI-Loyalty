# Security Follow-Up Tickets (from SEC-24 report)

Source: `docs/security/SECURITY_FINDINGS_REPORT.md` (Revision 1, 2026-10-13).
Drafts only - copy into the tracker. IDs are placeholders (`SEC-F##` = finding number).
There are no High findings; the single Critical (F-10) is already fixed in code and
closes with SEC-23.

---

## SEC-F01 - Configure trusted-proxy client-IP resolution

- **Severity:** Medium · **Finding:** F-01 · **Owner:** Backend Dev + Security Dev · **Est:** 3h
- **Problem:** Rate limits and the anonymous-stamp identity read `request.client.host`.
  The Dockerfile runs gunicorn+UvicornWorker with no `--forwarded-allow-ips`. Behind a
  load balancer/CDN every client may share one IP.
- **Acceptance criteria**
  - [ ] On staging, log `request.client.host` for two requests from different networks; they differ.
  - [ ] `FORWARDED_ALLOW_IPS` is set to the ingress addresses only (not `*`).
  - [ ] Test: 21st `POST /auth/customer/otp-request` in a minute from IP X returns 429 with `Retry-After`; a request from IP Y still succeeds.
  - [ ] Test: two anonymous scans from different IPs at one branch both get a stamp.
  - [ ] A spoofed `X-Forwarded-For` from an untrusted source is ignored.
- **Depends on:** a staging environment.

## SEC-F02 - Audit and lock down `quickbite_admin_bypass` grants

- **Severity:** Medium · **Finding:** F-02 · **Owner:** Security Dev · **Est:** 3h
- **Problem:** The `NOLOGIN BYPASSRLS` role's grants are spread over migrations 0010, 0015-0018 and enforced by convention only.
- **Acceptance criteria**
  - [ ] Staging dump of `information_schema.role_table_grants` for the role is attached.
  - [ ] The dump is diffed against migrations 0010/0015/0016/0017/0018; every extra grant is justified or revoked (new migration, never edit old ones).
  - [ ] Automated test fails if the role gains a grant not on an allowlist.
  - [ ] Test: from the role, `INSERT`/`DELETE` on tables where only `SELECT` is intended is denied.
  - [ ] Pentest plan row 6.3 moves from "Open gap" to "Ready".

## SEC-F03 - Implement loyalty anti-fraud (GPS spoofing, impossible travel)

- **Severity:** Medium · **Finding:** F-03 · **Owner:** Backend Dev · **Est:** 8h
- **Problem:** `app/core/anti_fraud.py` is a stub. The geofence trusts client coordinates; no velocity check.
- **Acceptance criteria**
  - [ ] Two scans by one customer at branches farther apart than is travelable in the elapsed time are flagged `is_fraudulent`.
  - [ ] A scan whose IP geolocation is implausibly far from the claimed GPS is flagged.
  - [ ] Flagged scans do not award stamps; each case has a unit test.
  - [ ] Pentest plan rows 8.1/8.2 are re-run and updated.
- **Note:** THREAT_MODEL §8.1/8.2/8.8 already tracks this; do not file as a new finding.

## SEC-F04 - Cap OTP send cost per tenant / add step-up

- **Severity:** Low · **Finding:** F-04 · **Owner:** Backend Dev · **Est:** 4h
- **Problem:** 20/min per IP still allows ~28,800 SMS/day from one IP across rotating numbers.
- **Acceptance criteria**
  - [ ] Per-tenant daily SMS budget; exceeding it returns 429 `OTP_BUDGET_EXCEEDED`, alert fires at 80%.
  - [ ] After N distinct identifiers from one IP in an hour, a CAPTCHA/step-up is required.
  - [ ] Tests for both; limits are settings, not constants.

## SEC-F08 - Rotate legacy 22-character QR tokens

- **Severity:** Low · **Finding:** F-08 · **Owner:** Backend Dev + restaurant owners · **Est:** 2h
- **Problem:** Branches created before SEC-14 keep 128-bit tokens; the ticket requires length >= 32.
- **Acceptance criteria**
  - [ ] Staging/prod: `SELECT length(qr_code_token), count(*) FROM restaurant.branches GROUP BY 1` shows only 43.
  - [ ] Each owner is told before rotation (rotating invalidates printed receipts); reprint plan agreed.
  - [ ] Rotation uses the existing audited `regenerate_qr_token` path.

## SEC-F00 - Re-run staging verification and re-issue the findings report

- **Severity:** Process · **Findings:** all · **Owner:** Security Dev · **Est:** 8h
- **Acceptance criteria**
  - [ ] SEC-23: `python scripts/verify_rls_isolation.py` exits 0 on staging with two seeded tenants; report saved.
  - [ ] SEC-21 (ZAP) and SEC-22 (Burp) executed; results added as findings.
  - [ ] Staging confirms F-06 (10 simultaneous scans -> 1 stamp), F-07 (OAuth against real Google), F-01.
  - [ ] `SECURITY_FINDINGS_REPORT.md` re-issued as Revision 2 and reviewed by >= 1 Backend Dev.
