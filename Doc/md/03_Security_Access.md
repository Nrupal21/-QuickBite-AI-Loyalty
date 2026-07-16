# QuickBite AI + Loyalty
## Security & Access Document · v2.0

> **Document 3 of 6** · Confidential · Adds Customer OTP Authentication. Research basis: Consensus.app

---

## 1. Authentication Methods

| Flow | Used By | Method |
|------|---------|--------|
| Flow 1 — Email + Password + TOTP MFA | Owner · Manager · Staff | bcrypt password → MFA session token → TOTP 6-digit → Full JWT + Refresh token |
| Flow 2 — OTP via Phone or Email ★ NEW | Loyalty Customers | Phone/email → Redis OTP (5-min TTL) → 6-digit verify → Customer JWT (7-day) |
| Flow 3 — Google OAuth 2.0 + PKCE | Owner (GMB connect + sign-in) | PKCE code challenge → Google callback → encrypted token storage → JWT pair |

### Flow 1 — Owner/Staff: Email + Password + TOTP MFA
- Passwords hashed using **bcrypt (work factor 12)**. Never stored in plaintext.
- On login: validate password → issue MFA session token (5-min TTL) → user enters TOTP code.
- TOTP secret stored **AES-256-GCM encrypted** in DB. Decrypted only during MFA verification.
- MFA is **MANDATORY** for Super Admin, Restaurant Owner, and Restaurant Manager.
- MFA is **OPTIONAL** for Staff. **NOT applicable** for Customer (they use OTP instead).
- JWT access token TTL: **15 minutes**. Refresh token: **30 days**. Single-use rotation on every refresh.

### Flow 2 — Customer OTP: Phone or Email ★ NEW

> 📚 **Research basis:** OTP significantly strengthens authentication beyond static passwords. Strict rate limiting and expiry are essential (Acosta Mayorga et al., 2025; Zhao et al., 2025).

#### Phone OTP Flow (Primary)

| # | Step | What Happens | Security Rule |
|---|------|-------------|---------------|
| 1 | Enter phone | Customer enters E.164 phone on loyalty login page. No password required. | Rate limit: 1 OTP request per phone per 2 minutes (Redis counter). |
| 2 | Phone lookup | `SHA-256(phone)` checked in customers table. | Phone never in query index. Always hashed lookup. |
| 3a | User found | Generate OTP → store `SHA-256(otp)` in Redis (5-min TTL) → send via Twilio SMS. | OTP via `Python secrets.token_digits(6)` — cryptographically random. NOT `random.randint()`. |
| 3b | User NOT found | Store phone in Redis temporarily → redirect to registration page. | Phone not written to DB until registration completes. No zombie records. |
| 4 | Enter OTP | Customer enters 6 digits. Auto-fill from SMS supported. | Max 3 attempts. Wrong code increments Redis counter. 3rd failure → OTP invalidated. |
| 5 | OTP verified | `SHA-256(submitted_otp)` compared to Redis hash → match → issue Customer JWT. | Customer JWT: `{customer_id, tenant_id, phone_hash, jti}`. TTL: 7 days. HttpOnly cookie. |
| 6 | Loyalty card | Stamp count, reward status, visit history all visible on authenticated card. | All data scoped to customer_id. Cannot see other customers' records. |

#### Seamless Registration — When OTP User Not Found

| # | Screen Element | What Happens | Data Stored |
|---|---------------|-------------|-------------|
| 1 | Phone pre-filled | Registration page opens with phone already in field. Message: 'Welcome! You're a new member.' | Phone from Redis temp session. Not re-entered. |
| 2 | Name field | First name required. Last name optional. | `encrypted_name` (AES-256-GCM) |
| 3 | Email field | Optional. Enables email OTP + reward email notifications. | `email_hash` (SHA-256) + `encrypted_email` (AES-256-GCM) |
| 4 | WhatsApp opt-in | 'Get reward alerts on WhatsApp?' Pre-ticked for Pro+ restaurants. | `whatsapp_opt_in` (boolean) |
| 5 | One-tap submit | Account created. OTP sent immediately. Customer verified and logged in in single flow. | `customers` row created. Customer JWT issued. |
| 6 | First stamp shown | 'Your first stamp is collected! 1/10 🎁' shown immediately after registration. | `StampLog` row created with `customer_id` now set. |

### Token Strategy Summary

| Token | Used By | TTL | Storage & Behaviour |
|-------|---------|-----|---------------------|
| Owner/Staff Access JWT | Owner, Manager, Staff | 15 minutes | Stateless. HS256. Contains: user_id, tenant_id, role, jti. Authorization header. |
| Owner/Staff Refresh Token | Owner, Manager, Staff | 30 days | Opaque. SHA-256 hash in DB + Redis. Single-use rotation. |
| MFA Session Token | Owner, Manager, Staff | 5 minutes | Temporary. Issued after password. Exchanged for JWT after TOTP. |
| **★ Customer Loyalty JWT** | Loyalty Customers | **7 days** | **Contains: customer_id, tenant_id, phone_hash, jti. HttpOnly cookie.** |
| **★ OTP Code (Redis)** | Loyalty Customers | **5 minutes** | **SHA-256(otp) in Redis. Auto-deleted on expiry or 3rd wrong attempt.** |
| JWT jti Revocation List | All users | Until expiry | Redis key per jti. Checked on every authenticated request. |

---

## 2. User Roles & Permissions

### Role Hierarchy

| Role | Scope | Level | MFA | Key Permissions |
|------|-------|-------|-----|-----------------|
| SUPER_ADMIN | All tenants (platform-wide) | 1 | REQUIRED | Full system access. All tenants. Billing override. All audit logs. |
| OWNER | Own restaurant only | 2 | REQUIRED | All restaurant functions. Team management. Billing. GMB. Loyalty config. |
| MANAGER | Own restaurant only | 3 | REQUIRED | Approve responses. Post to GMB. Campaigns. View all reviews and scans. |
| STAFF | Own restaurant only | 4 | OPTIONAL | Read-only dashboard. Verify reward redemption codes only. |
| CUSTOMER (auth) | Own loyalty account only | 5 | NONE — OTP | Track stamps. View loyalty card. Claim rewards. Update profile. |
| CUSTOMER (anon) | Public only | — | NONE | Submit review via QR. Collect anonymous stamp (not tracked). |

### Permission Matrix — Owner/Manager/Staff/Super Admin

| Action | SUPER | OWNER | MANAGER | STAFF | CUSTOMER |
|--------|:-----:|:-----:|:-------:|:-----:|:--------:|
| View own tenant's reviews | ✅ | ✅ | ✅ | ✅ | ❌ |
| Approve / reject AI responses | ✅ | ✅ | ✅ | ❌ | ❌ |
| Post response to Google My Business | ✅ | ✅ | ✅ | ❌ | ❌ |
| Configure loyalty reward programs | ✅ | ✅ | ✅ | ❌ | ❌ |
| Verify reward redemption codes | ✅ | ✅ | ✅ | ✅ | ❌ |
| Run SMS / WhatsApp campaigns | ✅ | ✅ | ✅ | ❌ | ❌ |
| Invite / remove team members | ✅ | ✅ | ❌ | ❌ | ❌ |
| Add or remove branches | ✅ | ✅ | ❌ | ❌ | ❌ |
| Connect Google My Business OAuth | ✅ | ✅ | ❌ | ❌ | ❌ |
| Manage subscription / billing | ✅ | ✅ | ❌ | ❌ | ❌ |
| View loyalty fraud log | ✅ | ✅ | ✅ | ❌ | ❌ |
| Manage ALL tenants (platform admin) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Force-logout any user | ✅ | ❌ | ❌ | ❌ | ❌ |
| Platform-wide audit logs | ✅ | ❌ | ❌ | ❌ | ❌ |

### ★ Customer Permission Matrix (Authenticated vs Anonymous)

| Customer Action | Auth Required? |
|----------------|:--------------:|
| Scan QR code (anonymous stamp — GPS checked, not linked to account) | ❌ NO |
| Track stamps persistently across multiple visits | ✅ YES — OTP login required |
| View loyalty card (stamp count, reward progress) | ✅ YES — OTP login required |
| Claim a reward (view redemption code) | ✅ YES — OTP login required |
| Submit a review via the review composer | ❌ NO — anonymous allowed |
| Copy review to Google Maps or Yelp | ❌ NO — anonymous allowed |
| Update name or email on profile | ✅ YES — OTP login required |
| Delete loyalty account | ✅ YES — OTP login required |
| Opt in/out of WhatsApp notifications | ✅ YES — OTP login required |

---

## 3. Row-Level Security Rules

> 🔒 **RLS is the last line of defence. Even a buggy `SELECT * FROM customers` only returns that tenant's loyalty customers.**

### How RLS Works
- Every tenant-scoped table has RLS enabled: `ALTER TABLE <table> ENABLE ROW LEVEL SECURITY`
- Policy: `USING (tenant_id = current_setting('app.tenant_id')::UUID)`
- On each authenticated request: JWT decoded → tenant_id extracted → set on DB session via SQLAlchemy event listener
- **Customer loyalty JWT also carries tenant_id.** Same RLS mechanism applies.
- Super Admin uses BYPASSRLS DB role — only active through `/admin` panel
- RLS policies are **version-controlled via Alembic migrations**. Cannot be silently disabled.

### Tables With RLS Enabled

| Table | RLS Scope | What This Means |
|-------|-----------|-----------------|
| users | tenant_id | Team members at Restaurant A can never see Restaurant B's accounts |
| **★ customers** | **tenant_id** | **Restaurant A's loyalty members invisible to Restaurant B** |
| branches | tenant_id | Branch locations are private per restaurant |
| customer_reviews | tenant_id | Reviews are private to the restaurant that received them |
| review_responses | tenant_id | AI drafts are private to the tenant |
| stamp_logs | tenant_id | Loyalty scan history is private per restaurant |
| reward_programs | tenant_id | Reward configurations are tenant-private |
| subscriptions | tenant_id | Tenants only see their own billing state |
| usage_tracking | tenant_id | Usage counters are tenant-private |
| audit_logs | tenant_id | Audit logs scoped to tenant. Super Admin sees all via bypass. |
| google_business_profiles | tenant_id | GMB OAuth tokens private to each restaurant |
| subscription_plans | (public) | No RLS — plan definitions are platform-wide |
| roles | (public) | No RLS — role definitions are platform-wide constants |

---

## 4. Error Handling Guide

### Owner/Staff Authentication Errors

| Scenario | HTTP Code | User Message | Internal Action |
|---------|:---------:|-------------|----------------|
| Wrong password (1st–4th attempt) | 401 | Invalid email or password. | Increment failed_login_count. Write to audit_log. |
| Wrong password (5th attempt in 15 min) | 429 | Too many attempts. Please wait 15 minutes. | IP blocked via SlowAPI for 15 minutes. |
| Account locked (10+ failures) | 423 | Account temporarily locked. Try again in 60 minutes. | Set locked_until. Send 'Suspicious activity' email. |
| Email not verified | 403 | Please verify your email. Check your inbox. | Offer 'Resend verification' link. |
| Invalid or expired JWT | 401 | Your session has expired. Please log in again. | Check jti against Redis revocation list. |
| Wrong TOTP code | 401 | Invalid verification code. | Increment MFA fail count. 3 fails → force re-login. |

### ★ Customer OTP Authentication Errors

| Scenario | HTTP Code | User Message | Internal Action |
|---------|:---------:|-------------|----------------|
| OTP requested — phone not found | 200 (redirect) | Welcome! You're new here. Tell us your name to get started. | Redirect to registration. Never reveal account doesn't exist (prevents enumeration). |
| OTP request rate-limited (< 2 min) | 429 | Please wait before requesting another code. | Redis counter: 1 request per 2 min per phone. Return Retry-After header. |
| OTP daily limit reached (> 5 today) | 429 | You've requested too many codes today. Try again tomorrow. | Redis 24-hour counter. Max 5 per phone per day. |
| OTP wrong code — 1st or 2nd attempt | 401 | That code is incorrect. You have X attempt(s) left. | Increment Redis attempt counter. Show remaining attempts. |
| OTP wrong code — 3rd attempt | 401 | Too many incorrect attempts. Please request a new code. | Delete OTP from Redis. Reset counter. User must restart. |
| OTP expired (> 5 minutes) | 400 | Your code has expired. Please request a new one. | Redis TTL auto-deleted. Return 'expired' error code. |
| Customer JWT expired (7 days) | 401 | Your session has expired. Please log in again. | Redirect to OTP login screen. |
| Registration — phone already exists | 409 | An account with this phone already exists. | Return 409. Offer 'Login instead' link with OTP flow. |
| Blocked customer tries to log in | 403 | Your account has been suspended. Please contact the restaurant. | `is_blocked=true`. No OTP sent. Support contact shown. |

### Loyalty & QR Errors

| Scenario | HTTP Code | User Message | Internal Action |
|---------|:---------:|-------------|----------------|
| QR code invalid or expired | 400 | This QR code is no longer active. | Log scan attempt. No stamp awarded. |
| GPS outside geofence | 403 | You need to be at the restaurant to collect a stamp. | Log as `is_fraudulent=true`. No stamp counted. |
| GPS permission denied | 400 | Please enable location access to collect your stamp. | Show 'How to enable location' guide. |
| Customer scanned within rate limit window | 429 | You've already collected your stamp today. | Redis rate limit per customer per branch per hour. |
| Reward code already used | 410 | This reward has already been claimed. | Log attempt to audit_logs. |
| Reward code expired | 410 | This reward has expired. | Log reward_expired event. |

### System & Integration Errors

| Scenario | HTTP Code | User Message | Internal Action |
|---------|:---------:|-------------|----------------|
| AI provider (OpenAI) timeout | 503 | Couldn't generate that right now. Please try again. | Fall back to Gemini via feature flag. Log to Sentry. |
| Both AI providers failed | 503 | Something went wrong. Please try again in a moment. | Alert ops team. Do not deduct usage. |
| Database connection lost | 500 | Something went wrong. Our team has been notified. | Sentry alert. Auto-reconnect. Health check fails. |
| Unhandled server exception | 500 | An unexpected error occurred. Reference: `<trace_id>`. | Full traceback to Sentry. Trace ID shown for support. |

---

## 5. Edge Cases

### ★ Customer OTP Edge Cases

| Edge Case | How We Handle It |
|-----------|----------------|
| Customer enters OTP with spaces ('123 456') | Strip non-digits client-side before submission. Both '123456' and '123 456' accepted. |
| SMS arrives after 5 minutes (slow network) | OTP expired. 'Your code has expired. Request a new one.' Immediate resend offered. |
| Customer opens OTP screen in two browser tabs | Redis atomic operations handle both tabs. Each attempt counted regardless of tab. |
| Customer registers with phone, then tries email OTP | Two separate lookup hashes. Different accounts. Account merge offered in v1.1. |
| OTP digit autocorrected on iOS | Numeric-only keyboard shown (`inputmode='numeric'`). Autocorrect disabled. |
| Attacker tries SIM-swap to intercept OTP | OTP TTL is 5 minutes. Single-use. Offer email OTP as recommended NIST mitigation (Bartłomiejczyk et al., 2024). |
| Customer deletes SMS before reading OTP | 'Resend code' button available after 2-minute rate-limit window. |
| Customer clears browser cookies (lost JWT) | Next visit: OTP login screen shown. Re-authenticate to restore loyalty card view. |
| Customer scans different restaurant's QR while logged in | JWT contains `tenant_id`. Different tenant's QR → clear session → show that restaurant's flow. |

### Loyalty & QR Edge Cases

| Edge Case | How We Handle It |
|-----------|----------------|
| Customer screenshots QR and scans from home | GPS geofence fails. `stamp_logs` row with `is_fraudulent=true`. No reward. |
| Customer scans 10 times in 10 minutes | Rate limit: 1 stamp per phone per branch per hour. Extras: 'Already collected today.' |
| Two customers claim same redemption code simultaneously | DB row lock on redemption. First wins. Second: 'This reward has already been claimed.' |
| Owner changes branch GPS coordinates | Owner updates lat/lng → PostGIS geometry auto-updates. Future scans use new coordinates. |
| Owner deletes branch with active loyalty customers | Soft-delete (`is_active=false`). Customers can still redeem existing rewards for 30 days. |
| WhatsApp opt-in, then customer uninstalls WhatsApp | Twilio returns failure → system marks `whatsapp_delivery_failed=true` → SMS fallback sent. |

---

## 6. OWASP Top-10 Compliance

| OWASP Risk | Status | Mitigation in QuickBite |
|-----------|--------|------------------------|
| A01: Broken Access Control | ✅ Implemented | RBAC + PostgreSQL RLS on all 15 tables + `require_role()` + `require_customer_session()` |
| A02: Cryptographic Failures | ✅ Implemented | AES-256-GCM for all PII. bcrypt w12 for passwords. HTTPS-only. Encryption key versioning. |
| A03: Injection (SQL + Prompt) | ✅ Implemented | SQLAlchemy ORM. `prompt_guard.py` for LLM inputs. Pydantic v2 validation on all inputs. |
| A04: Insecure Design | ✅ Documented | Architecture Decision Records (ADRs). Security design review before each phase launch. |
| A05: Security Misconfiguration | ✅ Automated | Startup config validation. `bandit` scan in CI. Hardened Docker (non-root, minimal base). |
| A06: Vulnerable Components | ✅ Automated | Dependabot weekly PR scans. `trivy` Docker image scan in CI. All versions pinned. |
| A07: Authentication Failures | ✅ Implemented | JWT rotation + TOTP MFA (Owner/Staff) + Customer OTP with rate limit + refresh token reuse detection. |
| A08: Software Integrity | ⚠️ Planned v1.1 | Signed Docker images + SBOM generation in CI pipeline. |
| A09: Logging & Monitoring | ✅ Implemented | `audit_logs` table + OpenTelemetry + Sentry alerts + Prometheus metrics. |
| A10: SSRF | ✅ Implemented | httpx with explicit URL allowlist. No user-controlled URLs fetched. |

---

*Document 3 of 6 · QuickBite AI + Loyalty · Security & Access v2.0 · Confidential*
