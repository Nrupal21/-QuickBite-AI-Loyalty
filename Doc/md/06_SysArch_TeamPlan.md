# QuickBite AI + Loyalty
## System Architecture, Developer Tasks & Team Plan · v1.2

> **Document 6 of 6** · Confidential · v1.2 adds STITCH-01→12 (Stitch.ai screens) + ANIM-01→06 (animation tickets) to sprint plan

---

## Research Findings (Consensus.app)

| Research Finding | Application in QuickBite |
|----------------|--------------------------|
| OTP authentication with TOTP + HOTP significantly strengthens 2FA (Acosta Mayorga et al., 2025) | Customer loyalty login uses OTP (phone SMS or email) as the primary auth method |
| SMS OTP implementations are vulnerable without rate limiting and expiry enforcement (Zhao et al., 2025) | Security Dev verifies: 5-min expiry, 3-attempt limit, 2-min rate limit, 5/day cap — all enforced correctly |
| MFA combining OTP + biometrics improves mobile financial app security (Ali et al., 2021) | Owner/Manager roles use TOTP (Google Authenticator) in addition to password |
| Customer PII in loyalty programs requires strong encryption under GDPR/CCPA (Amama et al., 2024) | Security Dev audits AES-256-GCM implementation, key entropy, IV randomness, and key rotation plan |
| Usability issues are the primary barrier to MFA adoption (Baseer, 2024) | Security Dev ensures friction is added only where necessary — not on customer-facing OTP flow |
| Digital loyalty programs with personalisation increase retention 20–40% (Kaur, 2024; Rachman et al., 2025) | Authenticated loyalty accounts built on security-first foundation approved by Security Dev |
| Simple digital membership systems significantly increase visit frequency (Ariyanti et al., 2025) | No app download required. Security Dev confirms QR + OTP is secure without native app constraints. |
| Blockchain loyalty programmes improve interoperability but have high complexity (Behrouzi et al., 2023) | Out of scope for v1. Traditional DB + RLS sufficient. Security Dev signs off on isolation model. |

---

## Section 1 — System Architecture

### 1.1 Architecture Overview

| Surface | Technology | Users |
|---------|-----------|-------|
| Customer PWA | Stitch.ai + Jinja2 + Tailwind CSS | Diners scanning QR codes — review + loyalty |
| Restaurant Dashboard | Stitch.ai + Jinja2 + Chart.js + GSAP | Owner / Manager / Staff |
| Super Admin Panel | Stitch.ai + Jinja2 | QuickBite internal team |

### 1.2 Full Component Map

| Component | Technology | Responsibilities |
|-----------|-----------|-----------------|
| Customer PWA | Stitch.ai + Jinja2 + Tailwind | Review composer, loyalty stamp card, OTP login flow, geofence request, stamp celebration, WhatsApp opt-in |
| Restaurant Dashboard | Stitch.ai + Jinja2 + Chart.js | Review approval workflow, loyalty analytics, QR download, branch management, subscription modal |
| Landing Page | Stitch.ai + Three.js + GSAP | 3D hero, scroll-driven narrative, pricing page |
| API Server | FastAPI 0.111 (Python 3.12) | All REST API + WebSocket. Business logic in /services. Routers are HTTP-only. |
| Auth Service | PyJWT + PyOTP + Passlib + Authlib | Password auth, TOTP MFA, OTP auth (customer), OAuth, refresh rotation |
| **★ OTP Auth (Customer)** | **Twilio SMS + SendGrid + Redis** | **6-digit OTP delivery. Redis stores SHA-256 hash with 5-min TTL.** |
| AI Engine | GPT-4o (primary) + Gemini 1.5 Pro (fallback) | Review + response draft generation. Feature-flag failover. |
| GMB Service | Google My Business API v4.9 | Incremental review sync, approved response posting |
| Geofence Service | PostGIS ST_Distance (GeoAlchemy2) | Validates customer GPS within branch geofence on every loyalty scan |
| QR Service | qrcode (Python) + Cloudflare R2 | Generates branch QR codes, uploads to R2, signed download URLs |
| Task Queue | Celery 5.3 + Redis broker + celery-beat | GMB sync (hourly), AI batch, SMS campaigns, usage resets, reward dispatch |
| Primary Database | PostgreSQL 16 via Supabase + PostGIS | 15 tables with RLS. Spatial queries for geofencing. |
| Cache + Sessions | Redis 7 (Upstash) | OTP storage (5-min TTL), JWT revocation, scan rate limiting, AI cache, feature flags |
| SMS + WhatsApp | Twilio | Customer OTP delivery, reward notifications (WhatsApp Business for Pro+), campaigns |
| Email | SendGrid | Email OTP delivery, verification, monthly reports, campaign sends |
| Billing | Stripe | Subscription plans, trials, upgrades. Webhook-driven. |
| **🔐 Security Layer** | **bandit + trivy + OWASP ZAP + Burp Suite** | **Static code analysis, Docker image scanning, dynamic pen testing — Security Dev owned** |
| Observability | OpenTelemetry + Sentry + Prometheus | Distributed tracing, error tracking, metrics dashboards |

### 1.3 Customer OTP Authentication Flow

```
1.  Customer scans QR → Browser opens https://qb.ai/q/{branch_token}
2.  Middleware: Branch token → tenant_id + branch_id (Redis lookup)
3.  Customer sees: Review + Stamp page (no login required for anonymous stamp)
4.  IF customer wants to TRACK stamps: taps 'Track my stamps →'
5.  OTP Login Screen: enters phone number
6.  API: POST /auth/customer/otp-request
    → Validate E.164 format (Security Dev: confirm regex is strict)
    → Hash phone (SHA-256) — Security Dev: verify no plaintext phone in index
    → Check customers table: found? → OTP to existing account
                              not found? → store phone in Redis → redirect to register
7.  Customer enters 6-digit OTP from SMS
8.  API: POST /auth/customer/otp-verify
    → Check Redis: SHA-256(otp) vs stored hash — Security Dev: verify hash comparison
    → Max 3 attempts; expire on fail — Security Dev: verify counter is atomic
    → Issue customer JWT (7-day TTL, HttpOnly cookie)
    → Security Dev: verify CUSTOMER_SECRET_KEY ≠ SECRET_KEY (separate signing keys)
9.  GPS permission requested → geofence_service checks distance
10. API: POST /loyalty/scan
    → Validate JWT + check rate limit (1 per phone per branch per hour)
    → PostGIS: ST_Distance(customer_gps, branch_location) < geofence_radius
    → If valid: INSERT stamp_logs row — Security Dev: RLS policy verified
11. Customer sees: 'Stamp Collected! 🎁 You have X/10 stamps'
12. Celery: process_loyalty_rewards → Twilio WhatsApp → SMS fallback
```

### 1.4 Security Architecture Boundaries

> 🔐 The Security Developer owns all layers marked below.

```
┌─────────────────────────────────────────────────────────┐
│  PERIMETER LAYER                                        │
│  🔐 Cloudflare WAF + Bot Detection + DDoS protection    │
│  🔐 TLS 1.3 only — enforced at edge                    │
├─────────────────────────────────────────────────────────┤
│  APPLICATION LAYER                                      │
│  🔐 SlowAPI rate limits (IP + tenant + OTP-specific)   │
│  🔐 Pydantic v2 input validation on ALL endpoints      │
│  🔐 prompt_guard.py — LLM injection prevention         │
│  🔐 Authlib PKCE flow for OAuth                        │
├─────────────────────────────────────────────────────────┤
│  AUTHENTICATION LAYER                                   │
│  🔐 PyJWT + bcrypt w12 + PyOTP (Owner/Staff)          │
│  🔐 Python secrets + SHA-256 hash (Customer OTP)       │
│  🔐 AES-256-GCM for all PII at rest                   │
│  🔐 Separate SECRET_KEY per user surface               │
├─────────────────────────────────────────────────────────┤
│  DATA LAYER                                             │
│  🔐 PostgreSQL RLS on all 15 tenant-scoped tables      │
│  🔐 SHA-256 hashed indexes (no raw PII in indexes)     │
│  🔐 AES-256-GCM encrypted columns (phone, email, name) │
├─────────────────────────────────────────────────────────┤
│  CI/CD SECURITY LAYER                                   │
│  🔐 bandit — Python static analysis on every PR        │
│  🔐 trivy — Docker image vulnerability scan            │
│  🔐 safety — dependency CVE scan                       │
│  🔐 git-secrets hook — prevents secret commits         │
│  🔐 OWASP ZAP — automated DAST on staging             │
└─────────────────────────────────────────────────────────┘
```

### 1.5 Stitch.ai Integration Pattern

| Step | Action | Detail |
|------|--------|--------|
| 1 | Write prompt | Include QuickBite hex codes, screen purpose, key interactions |
| 2 | Generate | Stitch.ai produces HTML + Tailwind. Review against Doc 4. |
| 3 | Security review | Security Dev checks: no inline JS, no exposed tokens in HTML, CSP-compatible markup |
| 4 | Export | Copy HTML to `app/templates/` subfolder |
| 5 | Wire | Replace static data with Jinja2 `{{ variable }}` tags. Add animations. |

---

## Section 2 — Developer Task Timeline

> **Team size: 7 people** — Backend Dev 1, Backend Dev 2, Frontend Dev, DevOps Engineer, QA Engineer, **Security Developer** (new), Product Manager

### 12-Week Sprint Overview

| Weeks | Phase | Key Deliverables | Status Gate |
|-------|-------|-----------------|-------------|
| 1–2 | Foundation | Project skeleton, auth, RBAC, multi-tenancy | Auth flows pass + SEC-01/02 complete |
| 3 | OTP + Billing | Customer OTP, registration, Stripe + **STITCH-01/02/03** (OTP screens) | OTP < 10s + screens wired + SEC-05→10 passed |
| 4–5 | Core Features | Review engine, loyalty + **STITCH-04/05/06** (review, loyalty, stamp screens) | E2E review + stamp flow + SEC-11→14 passed |
| 6 | Dashboard + Screens | DASH-01/02 + **STITCH-07/08/09** + ANIM-01/02 begin | Dashboard live + SEC-15/16 passed |
| 6–7 | **Animations ★ NEW** | **ANIM-01→06** (all screens animated) + STITCH-10/11/12 | All 12 screens animated + Three.js + GSAP on landing |
| 7–8 | Pen Testing + QA | Admin, full pen test, load test, security audit | P95 < 500ms + 0 critical security findings |
| 9–10 | Staging + Pilot | Deploy staging, pilot restaurants, security sign-off | Security Dev pre-launch checklist complete |
| 11–12 | Launch | Production deploy, security monitoring, incident response plan | Security Dev clears production sign-off |

---

### Weeks 1–2: Foundation

| Week | Ticket | Description | Assigned To |
|------|--------|-------------|------------|
| 1 | INFRA-01 | Project skeleton — FastAPI, folder structure, requirements, config | All devs |
| 1 | INFRA-02 | PostgreSQL + PostGIS + Alembic — engine, base models, migrations | Backend Dev 1 |
| 1 | INFRA-03 | Redis setup — cache service, connection pool, Celery broker | Backend Dev 2 |
| 1 | Stitch.ai | Setup workspace, import QuickBite design system, test first export | Frontend Dev |
| 1 | DevOps | Docker + Compose — full local stack | DevOps Eng |
| **1** | **🔐 SEC-01** | **Threat model: map all attack surfaces (OTP, JWT, RLS, PII, GMB OAuth, Stripe webhooks). Document threats and mitigations.** | **Security Dev** |
| **1** | **🔐 SEC-02** | **CI security toolchain: integrate `bandit`, `trivy`, `safety`, `git-secrets` into GitHub Actions. Block PRs on findings.** | **Security Dev** |
| 2 | AUTH-01 | User registration + email verification | Backend Dev 1 |
| 2 | AUTH-02 | Login + TOTP MFA flow | Backend Dev 1 |
| 2 | AUTH-03 | JWT refresh rotation | Backend Dev 1 |
| 2 | AUTH-04 | RBAC guards — require_role(), check_subscription_tier() | Backend Dev 2 |
| 2 | TENANT-01 | Subdomain middleware | Backend Dev 2 |
| 2 | TENANT-02 | PostgreSQL Row-Level Security policies | Backend Dev 2 |
| 2 | Stitch.ai | Login, MFA setup, registration screens | Frontend Dev |
| **2** | **🔐 SEC-03** | **Auth security audit: verify bcrypt w12, JWT HS256 (not 'none'), SECRET_KEY entropy ≥ 256-bit, TOTP RFC 6238 compliance, refresh rotation single-use, jti revocation in Redis.** | **Security Dev** |
| **2** | **🔐 SEC-04** | **RLS policy audit: write cross-tenant test queries. Verify `SELECT * FROM users` returns 0 rows for another tenant. Test with psql directly.** | **Security Dev** |

---

### Week 3: OTP + Billing ★ Security Dev Critical Week

| Ticket | Description | Assigned To |
|--------|-------------|------------|
| SUB-01 | Seed 3 plans + Stripe checkout + webhook handler | Backend Dev 1 |
| SUB-02 | Usage tracking + plan gating dependency | Backend Dev 2 |
| NEW-OTP-01 | POST /auth/customer/otp-request (phone + email variants) | Backend Dev 1 |
| NEW-OTP-02 | POST /auth/customer/otp-verify — hash check, attempt limit, JWT | Backend Dev 1 |
| NEW-OTP-03 | POST /customers/register — seamless flow, encrypted PII | Backend Dev 2 |
| Stitch.ai | OTP login screen + customer registration screen | Frontend Dev |
| **🔐 SEC-05** | **OTP crypto audit: verify `secrets.token_digits(6)` not `random.randint()`. Verify OTP stored as `SHA-256(otp)` not plaintext. Verify Redis TTL is exactly 300s.** | **Security Dev** |
| **🔐 SEC-06** | **OTP enumeration prevention: verify unknown phone returns HTTP 200 + `{"status":"new_user"}` — NOT 404. Prevents attacker from discovering registered phones.** | **Security Dev** |
| **🔐 SEC-07** | **OTP rate limit verification: confirm 3-attempt lockout deletes Redis key, 2-minute cooldown per phone, 5/day hard cap. Test with automated script.** | **Security Dev** |
| **🔐 SEC-08** | **Customer JWT isolation: verify `CUSTOMER_SECRET_KEY ≠ SECRET_KEY`. Verify customer JWT cannot be used on owner routes (and vice versa). Test with crafted tokens.** | **Security Dev** |
| **🔐 SEC-09** | **PII encryption audit: verify AES-256-GCM key is 256-bit, IV is random per encryption, no phone/email stored in plaintext anywhere in DB. Run SQL query to confirm.** | **Security Dev** |
| **🔐 SEC-10** | **Stripe webhook security: verify `stripe.Webhook.construct_event()` called before any processing. Test with invalid signature — must return 401.** | **Security Dev** |

---

### Weeks 4–6: Core Features (Parallel Tracks)

| Week | Backend Track | Frontend Track (Stitch.ai) | 🔐 Security Dev Track |
|------|--------------|---------------------------|-----------------------|
| 4 | REVIEW-01 (composer), REVIEW-03 (GMB OAuth), LOYALTY-01 (branch + GPS) | **STITCH-04** (review composer screen), wire STITCH-01/02/03 to APIs. ANIM-06 (Animate.css CDN in base.html). | **SEC-11:** Audit PKCE implementation in GMB OAuth (verify code_verifier is random, code_challenge is SHA-256). **SEC-12:** Review prompt_guard.py — test LLM injection via review tags. |
| 5 | LOYALTY-02 (QR), LOYALTY-03 (geofence + stamp), REVIEW-02 (AI drafts) | **STITCH-05** (loyalty card), **STITCH-06** (stamp collected). Wire Jinja2 stamp grid loop. ANIM-02 (star cascade) start. | **SEC-13:** Geofence fraud audit — verify `is_fraudulent=true` cannot be bypassed. Test with spoofed GPS coordinates. **SEC-14:** QR token entropy audit — verify `secrets.token_urlsafe(32)` used. Test QR token brute-force resistance. |
| 6 | LOYALTY-04 (rewards + redemption), DASH-01 (dashboard + WS), DASH-02 (analytics) | **STITCH-07** (dashboard), **STITCH-08** (branch+QR), **STITCH-09** (loyalty analytics). Wire all data. ANIM-01 + ANIM-04 begin. | **SEC-15:** Celery idempotency audit — verify duplicate reward dispatch cannot occur on retry. **SEC-16:** WebSocket security — verify JWT required to subscribe, tenant_id enforced on all pushed events. |

---

### Weeks 6–7: Animation Phase ★ Frontend Dev Lead

> **New in v1.2.** All ANIM tickets add animations to already-wired Stitch.ai screens. No new backend work. Security Dev continues SEC-15/16 in parallel during Week 6.

| Ticket | Animation | What Gets Animated | Library |
|--------|-----------|-------------------|---------|
| **ANIM-01** | OTP Screen Animations | Page load `slideInUp`. Error shake (`translateX`). 6-box success stagger. Resend timer colour `#64748B → #0D9488`. | Anime.js + Animate.css |
| **ANIM-02** | Review Composer Animations | Star fill cascade (stagger 60ms, `easeOutBack`). Tag chip `scale(1.0→1.1→1.0)`. AI draft card `fadeIn`. Copy `bounceIn`. | Anime.js + Animate.css |
| **ANIM-03** | Loyalty Card + Stamp Celebration | Stamp counter `0→N` 600ms. New stamp cell `spring(1,80,10,0)`. Progress bar 700ms. Reward unlock 3-step timeline. `canvas-confetti` burst. | Anime.js + canvas-confetti |
| **ANIM-04** | Dashboard Animations | Stat card count-up (800ms, 100ms stagger). Toast Anime.js auto-dismiss after 3s. WS notification `slideInRight`. Approval `fadeOutLeft`. | Anime.js + Animate.css |
| **ANIM-05** | Landing Page — Three.js + GSAP | Three.js r128: cel-shaded 3D model on `#hero-canvas`, lerped mouse tracking, `IntersectionObserver` pause. GSAP ScrollTrigger: card stagger-reveal, hero section pin. | Three.js r128 + GSAP 3.12 |
| **ANIM-06** | Animate.css Global Setup | CDN in `<head>` of `base.html`. Upgrade modal `zoomIn`. Skeleton loaders `pulse infinite`. `triggerShake(el)` re-triggerable helper via `void el.offsetWidth` reflow. | Animate.css 4.1.1 |

**Also in this phase:**

| Ticket | Screen | Details |
|--------|--------|---------|
| **STITCH-10** | Billing + Pricing | 3-column pricing. Pro highlighted (`scale-105 shadow-lg`). Export to `billing/pricing.html`. |
| **STITCH-11** | Super Admin Panel | Amber warning banner. Plan badges. Force Logout = red `#DC2626`. Export to `admin/index.html`. |
| **STITCH-12** | Landing Page Hero | `<canvas id="hero-canvas">` for Three.js. Inter 48px/800 headline. `.scroll-trigger-section` on feature cards for GSAP. Export to `landing/index.html`. |

> **Animation rules:** Animate.css loads in `<head>` (available before first render). Anime.js loads before `</body>` and before `app.js`. Three.js + GSAP loaded with `defer` on landing page only. Follow Doc 4 Section 7 decision matrix — never mix libraries for the same animation.

---

### Weeks 7–8: Penetration Testing + Polish ★ Security Dev Peak Sprint

| Week | Task | Owner |
|------|------|-------|
| 7 | ADMIN-01 — Super Admin panel | Backend Dev 1 |
| 7 | SUB-03 — Upgrade flow + Stripe billing portal | Backend Dev 2 |
| 7 | Admin panel screens, upgrade modal (Stitch.ai) | Frontend Dev |
| 7 | Integration test suite: auth flows, OTP flows, loyalty flows, RLS isolation | QA Engineer |
| **7** | **🔐 SEC-17 — Full Penetration Test Plan:** write test plan covering 8 attack categories below | **Security Dev** |
| **7** | **🔐 SEC-18 — Auth Bypass Tests:** JWT 'none' algorithm attack, algorithm confusion, expired token reuse, cross-role token, TOTP replay within window | **Security Dev** |
| **7** | **🔐 SEC-19 — OTP Attack Tests:** brute-force 3-attempt lockout, OTP replay after expiry, phone enumeration via timing, Redis key collision attempt | **Security Dev** |
| **7** | **🔐 SEC-20 — Injection Tests:** SQL injection via all Pydantic inputs, LLM prompt injection via review tags, XSS via restaurant name in HTML template | **Security Dev** |
| 8 | Load testing — Locust: 100 concurrent users on all key endpoints | DevOps |
| 8 | Bug fixes — triage and fix QA + load test issues | All devs |
| 8 | Performance — P95 < 500ms AI endpoint, < 10ms geofence | DevOps |
| **8** | **🔐 SEC-21 — OWASP ZAP Automated Scan:** run on staging. All High + Critical findings must be resolved before staging sign-off. | **Security Dev** |
| **8** | **🔐 SEC-22 — Burp Suite Manual Testing:** intercept OTP flow, replay attacks on loyalty scan, tamper with JWT tenant_id claim, SSRF via user-controlled URL inputs | **Security Dev** |
| **8** | **🔐 SEC-23 — RLS Cross-Tenant Final Verification:** log in as Tenant A, execute 10 different `SELECT *` queries, confirm 0 rows from Tenant B across all 15 RLS-protected tables | **Security Dev** |
| **8** | **🔐 SEC-24 — Security Findings Report:** document all findings with severity (Critical/High/Medium/Low), evidence, and remediation steps. Share with all devs. | **Security Dev** |

---

### Weeks 9–10: Staging + Pilot + Security Sign-Off

| Task | Owner |
|------|-------|
| Deploy to staging — full environment parity with production | DevOps |
| End-to-end user testing with real QR codes, real SMS OTPs, real GMB connection | All devs |
| Onboard 2–3 pilot restaurants. Collect real feedback. | PM |
| Fix all critical bugs from pilot. No new features in this phase. | All devs |
| **🔐 SEC-25 — Remediation Verification:** re-test all findings from SEC-24 report. All Critical and High findings must be resolved before launch sign-off. | **Security Dev** |
| **🔐 SEC-26 — Write SECURITY.md:** responsible disclosure policy, security contact email, known security scope | **Security Dev** |
| **🔐 SEC-27 — Write RUNBOOK.md:** incident response steps, secret rotation procedure, DB restore procedure, how to force-logout all users | **Security Dev** |
| **🔐 SEC-28 — Pre-Launch Security Checklist sign-off** (see checklist below) | **Security Dev** |

#### 🔐 Pre-Launch Security Checklist (Security Dev must sign off each item)

| # | Check | Status |
|---|-------|--------|
| 1 | OWASP Top-10 self-audit completed and documented | ☐ |
| 2 | All auth endpoints tested for brute-force, token reuse, and session fixation | ☐ |
| 3 | RLS verified: Tenant A cannot access Tenant B's data via any SQL path | ☐ |
| 4 | OTP enumeration prevention verified: unknown phone → 200 not 404 | ☐ |
| 5 | OTP crypto verified: `secrets.token_digits(6)` confirmed in code review | ☐ |
| 6 | `CUSTOMER_SECRET_KEY ≠ SECRET_KEY` verified in production env | ☐ |
| 7 | AES-256-GCM encryption: key is 256-bit, IV random per call, no plaintext PII in DB | ☐ |
| 8 | JWT revocation confirmed: revoked jti rejected within 15 seconds | ☐ |
| 9 | Refresh token reuse attack: replaying old token revokes all sessions | ☐ |
| 10 | All bandit CI scans green (no High/Critical findings) | ☐ |
| 11 | trivy Docker scan: no Critical CVEs in final image | ☐ |
| 12 | OWASP ZAP automated scan: no High/Critical findings | ☐ |
| 13 | Burp Suite manual testing: no critical auth bypass found | ☐ |
| 14 | SECURITY.md committed to repo | ☐ |
| 15 | RUNBOOK.md committed with incident response steps | ☐ |
| 16 | Sentry PII scrubbing verified: no phone/email in error events | ☐ |
| 17 | Stripe webhook signature verification confirmed in production | ☐ |

---

### Weeks 11–12: Production Launch + Security Monitoring

| Task | Owner |
|------|-------|
| Production deploy. Monitor error rate, P95 latency, queue depth in Grafana. | DevOps |
| First paying customer target. Sales outreach begins. | PM |
| **🔐 SEC-29 — Security Monitoring Setup:** Sentry alert rules for auth anomalies, Prometheus alert for OTP request spike (> 100/min = possible attack), Grafana dashboard for failed login rates | **Security Dev** |
| **🔐 SEC-30 — Incident Response Drills:** simulate a compromised JWT (force-logout all), simulate leaked SECRET_KEY (rotation drill), simulate DB restore | **Security Dev** |
| Week 12: Retrospective. Measure success metrics from PRD. | PM |
| Week 12: Plan v1.1 sprint (Scratch cards, WhatsApp, CRM campaigns, Yelp integration). | PM |
| **Ongoing:** Weekly `bandit` + `trivy` + `safety` scan results reviewed. 90-day `SECRET_KEY` rotation reminder. | **Security Dev** |

---

## Section 3 — Team Role Assignments

### Role Definitions (7 roles)

#### ⚙️ Backend Dev 1
**Owns:** Auth system (all flows: password, TOTP MFA, customer OTP, OAuth), AI engine (GPT-4o + Gemini), GMB service, OTP registration service, Billing service (Stripe)  
**Collaborates with:** Backend Dev 2 for schema design. Frontend Dev for API contracts. **Security Dev for auth PR reviews.**  
**Skills:** Python, FastAPI, SQLAlchemy, JWT, Twilio, OpenAI

---

#### 🗄️ Backend Dev 2
**Owns:** PostgreSQL schema + PostGIS + Alembic, Row-Level Security policies, Geofence service, Loyalty service (stamp logging, reward unlocking), Usage tracking + plan gating, Celery workers + Redis cache  
**Collaborates with:** Backend Dev 1 for auth + AI. DevOps for DB provisioning. **Security Dev for RLS policy review.**  
**Skills:** Python, PostgreSQL, PostGIS, GeoAlchemy2, Redis, Celery

---

#### 🎨 Frontend Dev
**Owns:** All screens via Stitch.ai — generate, refine, export. Review composer PWA, Customer OTP login + registration screens, Loyalty card + stamp animations, Dashboard layout + components, Landing page + 3D hero (Three.js + GSAP)  
**Collaborates with:** Backend devs for API endpoints. PM for UX review. **Security Dev for CSP-compatible Stitch.ai output review.**  
**Skills:** Stitch.ai, Jinja2, Tailwind CSS, JavaScript, GSAP, Three.js, Chart.js

---

#### 🐳 DevOps Engineer
**Owns:** Docker + docker-compose, GitHub Actions CI/CD, Staging + production deployment, Monitoring (Prometheus + Grafana + Sentry), Secret management (AWS Secrets Manager), Database backups  
**Collaborates with:** **Security Dev for CI security tool integration, secrets management, and production hardening.**  
**Skills:** Docker, GitHub Actions, Nginx, AWS, Terraform, Grafana

---

#### 🔍 QA Engineer
**Owns:** Integration test suite, OTP flow tests, MFA bypass test, RLS cross-tenant isolation tests, Load testing (Locust), Pre-launch functional checklist  
**Collaborates with:** **Security Dev (QA tests functional correctness, Security Dev tests attack vectors — complementary, not overlapping).**  
**Skills:** pytest, Locust, Postman, httpx

---

#### 🔐 Security Developer ★ NEW
**Owns:**
- Threat modeling and attack surface documentation
- OTP security audit (crypto randomness, rate limiting, enumeration prevention, hash storage)
- JWT security audit (algorithm, key separation, revocation, refresh rotation)
- PII encryption audit (AES-256-GCM key entropy, IV randomness, no plaintext in DB)
- PostgreSQL RLS policy correctness verification (cross-tenant SQL tests)
- PKCE OAuth implementation review
- Prompt injection prevention review (prompt_guard.py)
- CI security toolchain: bandit, trivy, safety, git-secrets
- Full penetration testing (Weeks 7–8): auth bypass, OTP attacks, injection, SSRF, replay attacks
- OWASP ZAP automated dynamic scan
- Burp Suite manual pen testing on auth and OTP flows
- Security findings report with severity triage
- Pre-launch security checklist sign-off (17-point)
- SECURITY.md and RUNBOOK.md authoring
- Production security monitoring alerts (Sentry + Prometheus)
- Incident response drills

**Collaborates with:** All developers — every auth-related PR requires Security Dev review before merge. Works closely with DevOps on secrets management and CI hardening.  
**Skills:** Python, SQL, OWASP methodology, Burp Suite, OWASP ZAP, bandit, trivy, cryptography, JWT attacks, SQL injection testing, threat modeling

**Critical security rules this role enforces:**
- ❌ No PR modifying auth, OTP, or encryption code merges without Security Dev approval
- ❌ No plaintext secrets in code, logs, or error messages
- ❌ No use of `random.randint()` for OTP — must be `secrets.token_digits()`
- ❌ No plaintext PII in database columns or query indexes
- ✅ All security findings must be documented, tracked, and re-tested before closure

---

#### 📋 Product Manager
**Owns:** Sprint planning + ticket prioritisation, Pilot restaurant onboarding, UX review of all Stitch.ai-generated screens, Success metric tracking, Stakeholder communication, v1.1 roadmap after launch  
**Collaborates with:** **Security Dev for security milestone gates — Security Dev must sign off before each phase transition.**  
**Skills:** Jira/Linear, Figma (for UX review), analytics tools

---

### Task Assignment Matrix (RACI)

> R = Responsible  A = Accountable  C = Consulted  I = Informed

| Ticket / Task | Backend 1 | Backend 2 | Frontend | DevOps | QA | 🔐 Security Dev |
|---------------|:---------:|:---------:|:--------:|:------:|:--:|:--------------:|
| INFRA-01 Project Skeleton | R | C | C | R | I | **C** |
| INFRA-02 PostgreSQL + PostGIS | C | R | I | C | I | **C** |
| INFRA-03 Redis + Celery | C | R | I | C | I | **C** |
| **SEC-01 Threat Model** | C | C | I | I | I | **R** |
| **SEC-02 CI Security Toolchain** | I | I | I | C | I | **R** |
| AUTH-01/02/03 Owner/Staff Auth | R | C | C | I | A | **A** |
| **SEC-03 Auth Security Audit** | C | I | I | I | C | **R** |
| AUTH-04 RBAC Guards | R | C | I | I | A | **A** |
| **SEC-04 RLS Policy Audit** | I | C | I | I | C | **R** |
| NEW-OTP-01/02 Customer OTP | R | C | C | I | A | **A** |
| **SEC-05 OTP Crypto Audit** | C | I | I | I | C | **R** |
| **SEC-06 OTP Enumeration Prevention** | C | I | I | I | C | **R** |
| **SEC-07 OTP Rate Limit Verification** | C | I | I | I | C | **R** |
| **SEC-08 Customer JWT Isolation** | C | I | I | I | C | **R** |
| **SEC-09 PII Encryption Audit** | C | C | I | I | C | **R** |
| **SEC-10 Stripe Webhook Security** | C | I | I | I | C | **R** |
| NEW-OTP-03 Customer Registration | R | C | C | I | A | **A** |
| TENANT-01/02 Multi-Tenancy | C | R | I | C | A | **C** |
| SUB-01/02/03 Billing + Gating | R | C | C | I | A | C |
| REVIEW-01 Review Composer API | R | C | I | I | A | I |
| REVIEW-02 AI Response Drafting | R | C | I | I | A | I |
| REVIEW-03 GMB OAuth + Sync | R | C | I | I | A | **A** |
| **SEC-11 GMB PKCE Audit** | C | I | I | I | C | **R** |
| **SEC-12 Prompt Injection Review** | C | I | I | I | C | **R** |
| LOYALTY-01/02 Branch + QR | C | R | C | I | A | **C** |
| LOYALTY-03 Geofence + Stamp Log | C | R | I | I | A | **C** |
| **SEC-13 Geofence Fraud Audit** | I | C | I | I | C | **R** |
| **SEC-14 QR Token Entropy Audit** | I | C | I | I | C | **R** |
| LOYALTY-04 Rewards + WhatsApp | R | C | I | I | A | I |
| **SEC-15 Celery Idempotency Audit** | C | C | I | I | C | **R** |
| **SEC-16 WebSocket Security** | C | C | I | I | C | **R** |
| DASH-01/02 Dashboard + Analytics | C | C | R | I | A | I |
| Stitch.ai All Screens | I | I | R | I | C | **C** |
| ADMIN-01 Super Admin Panel | R | C | C | I | A | **A** |
| Docker + CI/CD Pipeline | C | C | I | R | C | **C** |
| Load Testing (Locust) | I | C | I | R | R | I |
| **SEC-17 to SEC-24 Full Pen Test** | C | C | I | C | C | **R** |
| **SEC-21 OWASP ZAP Scan** | I | I | I | C | C | **R** |
| **SEC-22 Burp Suite Manual Test** | I | I | I | I | C | **R** |
| **SEC-23 RLS Cross-Tenant Final** | I | C | I | I | C | **R** |
| **SEC-24 Security Findings Report** | A | A | A | A | A | **R** |
| **SEC-25 Remediation Verification** | C | C | I | I | C | **R** |
| **SEC-28 Pre-Launch Checklist** | A | A | A | A | A | **R** |
| **SEC-29 Security Monitoring** | I | I | I | C | I | **R** |
| STITCH-01/02/03 OTP Screens | I | I | **R** | I | C | C |
| STITCH-04 Review Composer | I | I | **R** | I | C | I |
| STITCH-05/06 Loyalty Screens | I | C | **R** | I | C | I |
| STITCH-07/08/09 Dashboard Screens | I | C | **R** | I | C | I |
| STITCH-10/11/12 Billing+Admin+Landing | C | I | **R** | I | C | C |
| ANIM-01 OTP Animations | I | I | **R** | I | C | I |
| ANIM-02 Review Composer Animations | I | I | **R** | I | C | I |
| ANIM-03 Loyalty + Stamp Celebration | I | I | **R** | I | C | I |
| ANIM-04 Dashboard Animations | I | I | **R** | I | C | I |
| ANIM-05 Three.js + GSAP Landing Page | I | I | **R** | I | C | I |
| ANIM-06 Animate.css Global Setup | I | I | **R** | I | C | I |
| Pre-launch Functional Checklist | A | A | A | A | R | **A** |

---

### Collaboration Rules (Updated for 7-Person Team)

| Rule | How It Works |
|------|-------------|
| Daily standup (15 min) | All 7 team members. Cover: what I built, what I'm building today, blockers. |
| API Contract First | Backend Dev defines Pydantic schemas before writing code. Frontend Dev reviews. No guessing. |
| Stitch.ai Review Gate | Every Stitch.ai-generated screen reviewed by PM before wiring to API. Security Dev checks for CSP issues. |
| **🔐 Security PR Gate** | **Every PR modifying auth, OTP, encryption, or RLS code requires Security Dev approval before merge. No exceptions.** |
| PR required for main | No direct pushes to main. Every change requires PR with at least 1 reviewer. CI must pass (including bandit). |
| Ticket states | todo → in-progress → in-review → done. No ticket stays 'in-progress' > 3 days without standup flag. |
| Blocked = escalate immediately | Any blocker > 4 hours is escalated to PM. Sprint velocity is protected. |
| **🔐 Security findings = stop-work** | **If Security Dev raises a Critical or High finding, work on the affected feature stops until remediated. No exceptions.** |
| Env variables | Never commit secrets. `git-secrets` hook on all commits. DevOps manages AWS Secrets Manager. Security Dev audits. |
| **🔐 Phase gate sign-off** | **Security Dev must sign off before each phase transition: Foundation → OTP week, OTP week → Core Features, Core Features → Pen Test, Pen Test → Staging, Staging → Production.** |

---

*Document 6 of 6 · QuickBite AI + Loyalty · System Architecture, Developer Tasks & Team Plan · v1.2 · Confidential*

> See **Doc 8 — Developer & AI Agent Working Guide** (`DevGuide_AI_Agents_IDE_Workflow.docx` / `08_Dev_Guide.md`) for: 13 AI agents with roles, IDE setup, feature branch workflow, detailed hour estimates, and progress report templates.
