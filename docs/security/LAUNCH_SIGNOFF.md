# Pre-Launch Security Checklist Sign-Off

**Ticket:** SEC-28 · **Status:** ⛔ NOT SIGNED — do not deploy to production
against this document. **No production deploy without a fully signed version
of this document** (per SEC-28's own acceptance criteria).

## Where the "17 items" come from

SEC-28's acceptance criteria say to verify "all 17 items of the pre-launch
security checklist (see Doc 6 Section 2)." Doc 6 §2 does not itself enumerate
17 discrete items — it only names the Week 9–10 activity as "Pre-launch
security checklist sign-off (17-point)" without a list. The 17 items below
were therefore **constructed for this document** from:

- The 15 preceding SEC tickets this one gates on (SEC-01, SEC-17 through
  SEC-27) — one checklist item per ticket's completion, and
- The two additional checks SEC-28's own acceptance criteria name explicitly
  (JWT revocation timing, Sentry PII scrubbing) that aren't otherwise tracked
  as their own ticket.

That's 15 + 2 = 17. **If the actual intended 17-point list exists elsewhere
(a spreadsheet, a Doc 6 revision not yet merged, something Security Dev has
independently), reconcile this table against it before treating this as
final** — this construction is a reasonable-effort reconstruction, not a
rediscovery of an original source.

## Current State: Nothing Below Is Verified

Every item is `Not Started` because the tickets it depends on
(SEC-01 through SEC-27) have no recorded completion — `git log --grep`
across every SEC-01 through SEC-29 ticket ID returns no matching commits as
of this writing, and `THREAT_MODEL.md` (SEC-01) is still in Draft. This
document exists so the checklist has a home and a shape; it does not attest
that launch is ready. **Do not fill in a signature on this document without
each row's evidence actually existing** — a signed row with no evidence
behind it is worse than an honestly-empty one.

---

## Checklist

| # | Item | Source | Status | Evidence | Verified by | Date |
|---|------|--------|--------|----------|--------------|------|
| 1 | Threat model (`docs/THREAT_MODEL.md`) reviewed and signed off by Backend Dev | SEC-01 | 🔴 Not Started — currently "Draft, pending review," with open gaps in its own Summary section | — | — | — |
| 2 | Penetration test plan (`docs/PENTEST_PLAN.md`) written, covers 8 categories × ≥3 cases, reviewed by ≥1 Backend Dev | SEC-17 | 🟡 Plan written (this milestone) — Backend Dev review still pending | `docs/PENTEST_PLAN.md` | — | — |
| 3 | Auth bypass penetration tests executed (alg:none, algorithm confusion, expired token, cross-role, TOTP replay, force-logout bypass) — all 6 documented | SEC-18 | 🟡 Test file exists (`tests/security/test_sec18_auth_bypass.py`) — not yet run against staging/SEC-24 report | `tests/security/test_sec18_auth_bypass.py` | — | — |
| 4 | OTP attack penetration tests executed (brute-force, replay-after-expiry, timing analysis, daily-limit bypass) | SEC-19 | 🟡 Test file exists (`tests/security/test_sec19_otp_attacks.py`); timing analysis explicitly requires a live-staging run, not just unit tests | `tests/security/test_sec19_otp_attacks.py` | — | — |
| 5 | Injection/SSRF penetration tests executed (SQLi, LLM injection, XSS, SSRF) | SEC-20 | 🟡 Test file exists (`tests/security/test_sec20_injection_attacks.py`); SSRF cases blocked pending an allowlist implementation — see `PENTEST_PLAN.md` §4 | `tests/security/test_sec20_injection_attacks.py` | — | — |
| 6 | OWASP ZAP automated scan against staging — 0 Critical/High (or ticketed remediation) | SEC-21 | 🔴 Not Started — requires a staging deployment | `docs/security/zap_report_v1.html` (not yet created) | — | — |
| 7 | Burp Suite manual penetration testing (OTP/JWT/webhook tamper, loyalty scan interception) | SEC-22 | 🔴 Not Started — requires a staging deployment | — | — | — |
| 8 | RLS cross-tenant final verification on staging — 15 tables × 0 cross-tenant rows, via raw SQL and via API | SEC-23 | 🔴 Not Started — requires 2 real pilot-tenant staging accounts | — | — | — |
| 9 | Security Findings Report compiled (SEC-18–23), severity-sorted, fix tickets filed for Critical/High | SEC-24 | 🔴 Not Started — depends on items 3–8 | — | — | — |
| 10 | All Critical/High findings from item 9 re-tested and marked `FIXED`; 0 remain `OPEN` | SEC-25 | 🔴 Not Started — depends on item 9 | — | — | — |
| 11 | `SECURITY.md` committed to repo root; GitHub private-vulnerability-reporting / Security Advisories enabled | SEC-26 | 🟡 `SECURITY.md` committed (this milestone). GitHub Advisories feature is a repo-admin setting this document cannot enable — **manual action required**: Settings → Security → Private vulnerability reporting | `/SECURITY.md` | — | — |
| 12 | `RUNBOOK.md` committed with all 6 scenarios; `SECRET_KEY` rotation and force-logout-all drilled in staging; DB PITR restore drilled on a clone; reviewed by DevOps + 1 Backend Dev | SEC-27 | 🟡 Document written (this milestone) with all 6 scenarios — **none of the 3 required drills have run yet**, and DevOps/Backend Dev review is pending. This is the item most likely to block launch the longest, since it depends on scheduled off-peak drills (SEC-30), not just writing | `docs/RUNBOOK.md` | — | — |
| 13 | `SECRET_KEY` rotation tested in staging with zero API downtime | SEC-27 / SEC-30 (Drill 2) | 🔴 Not Started — and per `RUNBOOK.md` §2, rotation as currently implemented invalidates every owner/staff session simultaneously (no `kid`/dual-key support); confirm this trade-off is accepted as-is before signing, don't assume the drill will reveal something different from what the code already shows | — | — | — |
| 14 | Force-logout-all drill: all sessions invalidated within 15 seconds | SEC-30 (Drill 1) | 🔴 Not Started | — | — | — |
| 15 | DB PITR restore drill on a clone: completes within SLA (< 30 min for a 1-hour-ago restore) | SEC-30 (Drill 3) | 🔴 Not Started — no in-repo backup/restore tooling exists; this is entirely dependent on Supabase's PITR feature and has never been exercised, per `docs/RUNBOOK.md` §6 | — | — | — |
| 16 | JWT revocation verified end-to-end in < 15 seconds (force-logout or single-session revoke to actual request rejection) | SEC-28 (named directly in its own acceptance criteria) | 🔴 Not Started — the mechanism exists and is unit-tested (Redis `revoked_jti` check, `tokens_valid_from` watermark), but the *timing* claim (< 15s) has not been measured end-to-end against a live deployment | — | — | — |
| 17 | Sentry PII scrubbing verified — no phone number or email appears in any captured error event | SEC-28 (named directly in its own acceptance criteria) | 🔴 Not Started — and currently **untestable as written**: `sentry_sdk.init()` is never called anywhere in `app/` (only `settings.SENTRY_DSN` exists as an unused config field, per SEC-29 being unstarted). This item cannot be verified until Sentry is actually wired up — treat it as blocked on SEC-29's Sentry setup, not just "not yet run" | — | — | — |

**Legend:** 🔴 Not Started · 🟡 In Progress / Partially Done · 🟢 Verified — Evidence Attached

---

## Explicit Non-Readiness Statement

As of the date below, **items 6, 7, 8, 9, 10, 13, 14, 15, 16, and 17 are not
started**, and items 2, 3, 4, 5, 11, 12 are partially complete (documents/test
scaffolds exist; execution, review, and drills do not). Per this ticket's own
acceptance criteria ("No production deploy without this signed document"),
**this document must not be signed, and production deploy must not proceed,
until every row above shows 🟢 with attached evidence.**

## Sign-off

*(To be completed only when every checklist item above is 🟢. Do not
pre-fill names/dates.)*

| Role | Name | Date | Attestation |
|------|------|------|-------------|
| Security Dev | _pending_ | _pending_ | "SEC-28 PASSED — all 17 items verified in staging" |

## Change Log

| Date | Change |
|------|--------|
| 2026-09-17 | Document created; checklist constructed from SEC-01/17-27 dependency tickets + SEC-28's own two named checks. All items marked per their actual current state (mostly Not Started). No item signed. |
