# QuickBite AI + Loyalty
## Technical Architecture Document · v3.0 (Database section ★)

> **Document 2 of 6** · Confidential · Includes OTP Customer Auth + Stitch.ai Frontend

---

## What Is This Doc?

This is the engineering blueprint of QuickBite AI + Loyalty. It tells any developer — or AI coding assistant — exactly what tools are being used, how the project is organized, and how data is structured.

> ⚠️ **Rule:** Never let an AI write a database model or choose a library without referencing this document first. Paste this into every new AI coding session.

---

## 1. Tech Stack

### Backend

| Technology | Version | Role | Why This Choice |
|-----------|---------|------|----------------|
| FastAPI | 0.111 (Python 3.12) | Core API framework | Async-native, auto OpenAPI docs, Pydantic v2 validation, WebSocket support |
| Uvicorn + Gunicorn | 0.29 / 22.x | ASGI server | Uvicorn handles async requests. Gunicorn manages multi-worker processes in production |
| Pydantic v2 | 2.7 | Data validation | All API requests and responses validated automatically. Type-safe throughout |
| SQLAlchemy | 2.x async | ORM | Async sessions, typed models. Industry standard for Python SaaS |
| Alembic | 1.13 | DB migrations | Version-controlled schema changes. Every change is a migration file |
| Celery + celery-beat | 5.3 | Task queue | Background jobs: GMB sync, AI batch, SMS campaigns, usage resets, reward dispatch |
| Structlog | 24.x | Logging | JSON logs with trace_id, tenant_id, user_id on every line |

### Frontend — Stitch.ai ★ PRIMARY PLATFORM

| Technology | Version | Role | Why This Choice |
|-----------|---------|------|----------------|
| **Stitch.ai** | Current | AI frontend generation | **Primary platform.** Generates clean HTML + Tailwind CSS from text prompts. Reduces dev time ~60%. Exports directly into app/templates/ |
| Jinja2 | 3.1 | SSR template engine | Stitch.ai exports wired to FastAPI backend via Jinja2 `{{ }}` template tags for dynamic data |
| Tailwind CSS | 3.4 | Utility-first styles | Design system tokens from Doc 4 used in all Stitch.ai prompts for consistency |
| GSAP + ScrollTrigger | 3.12 | Scroll animations | Landing page scroll-driven narrative. Added manually to Stitch.ai exported HTML |
| Three.js | r128 | 3D WebGL hero — **Landing page ONLY** | Cel-shaded 3D restaurant model. Mouse-reactive. Falls back to static image. |
| **Anime.js** | **3.2.1 ★ NEW** | **Complex JS timelines — All customer + dashboard screens** | Reward unlock, stamp counter, progress bar, star cascade, OTP stagger. |
| **Animate.css** | **4.1.1 ★ NEW** | **Utility CSS animations — All screens** | fadeInUp, shakeX, slideInRight, bounceIn, zoomIn, pulse infinite. |
| Chart.js | 4.x | Dashboard charts | Sentiment trend charts, scan heatmaps in owner dashboard |

### Database & Spatial

| Technology | Version | Role | Why This Choice |
|-----------|---------|------|----------------|
| PostgreSQL | 16 via Supabase | Primary database | Row-Level Security for tenant isolation. JSONB for config. PostGIS for geofencing |
| PostGIS | 3.4 | Spatial extension | GPS geofencing for loyalty stamp validation. ST_Distance / ST_DWithin queries |
| Redis | 7 (Upstash) | Cache + broker + OTP | JWT revocation, Celery broker, rate limiting, OTP storage (5-min TTL), scan throttle, AI cache |
| Asyncpg | 0.29 | PG async driver | Required for SQLAlchemy 2.x async engine. Significantly faster than psycopg2 |

### Authentication & Security

| Technology | Version | Role | Why This Choice |
|-----------|---------|------|----------------|
| PyJWT | 2.8 | JWT tokens | 15-min owner/staff tokens. 7-day customer tokens. jti for revocation |
| Passlib + bcrypt | 1.7 / w12 | Password hashing | Owner/Manager/Staff passwords. bcrypt work factor 12. Never reversibly stored |
| PyOTP | 2.9 | TOTP MFA | RFC 6238. Google Authenticator for Owner/Manager login |
| **Python secrets** | stdlib | **OTP generation** | **`secrets.token_digits(6)` for customer OTP. NOT `random.randint()` — cryptographically required** |
| Authlib | 1.3 | OAuth 2.0 + PKCE | Google Sign-In + GMB OAuth. PKCE prevents auth code interception |
| SlowAPI | 0.1 | Rate limiting | Per-IP + per-tenant + per-customer (OTP rate limiting). Brute-force protection |
| zxcvbn-python | 4.4 | Password strength | Catches weak patterns. Required for Owner/Manager passwords |

### Customer OTP Service ★ NEW

| Technology | Version | Role | Why This Choice |
|-----------|---------|------|----------------|
| Twilio SMS | 2010-04-01 REST | Phone OTP delivery | Customer loyalty login via 6-digit SMS code |
| SendGrid Email OTP | v3 API | Email OTP delivery | Alternative OTP channel for customers preferring email |
| Redis OTP Store | — | OTP persistence | OTP stored as `SHA-256(otp)` in Redis. Key: `otp:{tenant_id}:{phone_hash}`. Auto-expires in 300s |

### AI & External Integrations

| Technology | Role | Why This Choice |
|-----------|------|----------------|
| OpenAI GPT-4o | Primary AI | Best natural-language quality for review drafts and response generation |
| Google Gemini 1.5 Pro | Fallback AI | Auto-activated if OpenAI fails. Hot-swap via Redis feature flag |
| Twilio WhatsApp | Reward notifications | WhatsApp alerts when loyalty reward unlocked (Pro+ only) |
| SendGrid | Transactional email | Verification, password reset, monthly reports, campaign sends |
| Google My Business API v4.9 | Review sync | Fetch reviews + post approved responses. Incremental cursor sync |
| Stripe 2024-04-10 | Billing | SaaS subscriptions, trials, upgrades, webhooks |
| qrcode (Python) 7.4 | QR generation | Branch-specific QR codes for loyalty + review composer |
| GeoAlchemy2 0.14 | Spatial ORM | SQLAlchemy bindings for PostGIS geofence queries |

### DevOps & Observability

| Technology | Role |
|-----------|------|
| Docker + Compose 26.x | Containerisation — dev/prod parity |
| GitHub Actions | CI/CD: lint → test → bandit → Docker build → staging → manual prod gate |
| OpenTelemetry + Jaeger 1.x | Distributed tracing across FastAPI → Celery → external APIs |
| Sentry 2.x | Error tracking with tenant_id + user_id context |
| Prometheus + Grafana | Metrics dashboards — request rate, error rate, OTP delivery time, queue depth |

---

## 2. File & Folder Structure

> 📋 **Copy this into your AI prompt:** *"All new files must follow this folder structure exactly. Do not create files outside these paths."*

```
quickbite/
├── app/
│   ├── api/v1/
│   │   ├── routers/
│   │   │   ├── auth.py              ← Owner/Staff: /auth/register, /auth/login, /auth/mfa/*
│   │   │   ├── customer_auth.py     ← ★ NEW: Customer OTP: /auth/customer/otp-request, otp-verify
│   │   │   ├── customers.py         ← ★ NEW: /customers/register, /customers/me, /customers/delete
│   │   │   ├── reputation.py        ← /reviews/*, /dashboard/*, /gmb/*
│   │   │   ├── loyalty.py           ← /loyalty/scan, /loyalty/card/{id}, /loyalty/menu/{branch_id}
│   │   │   ├── marketing.py         ← /campaigns/*
│   │   │   ├── billing.py           ← /billing/*, /webhooks/stripe
│   │   │   └── admin.py             ← /admin/* (Super Admin only)
│   │   └── dependencies/
│   │       ├── auth.py              ← get_current_user, require_role()
│   │       ├── customer_auth.py     ← ★ NEW: get_current_customer, require_customer_session()
│   │       └── subscription.py      ← check_plan_limit(), track_usage()
│   │
│   ├── core/
│   │   ├── config.py                ← Pydantic BaseSettings — all env vars inc. OTP settings
│   │   ├── security.py              ← Owner/Staff: JWT, bcrypt, TOTP, refresh rotation
│   │   ├── customer_security.py     ← ★ NEW: Customer JWT, OTP crypto, rate limit helpers
│   │   ├── rbac.py                  ← Role definitions + permission matrix
│   │   ├── encryption.py            ← AES-256-GCM + key versioning for all PII
│   │   ├── rate_limiter.py          ← SlowAPI: IP + tenant + OTP-specific limits
│   │   ├── anti_fraud.py            ← Loyalty scan fraud detection
│   │   └── prompt_guard.py          ← LLM input sanitisation
│   │
│   ├── db/
│   │   ├── base.py                  ← UUID PKs + created_at/updated_at
│   │   ├── session.py               ← Async engine + get_db()
│   │   ├── rls.py                   ← PostgreSQL RLS policies
│   │   └── models/
│   │       ├── user.py              ← User, Role, Session (Owner/Manager/Staff)
│   │       ├── customer.py          ← ★ NEW: Customer (loyalty member with encrypted PII)
│   │       ├── tenant.py            ← Tenant, TenantSettings
│   │       ├── branch.py            ← Branch (PostGIS geometry, QR token)
│   │       ├── reputation.py        ← GMBProfile, CustomerReview, ReviewResponse
│   │       ├── loyalty.py           ← RewardProgram, StampLog, ScratchCard
│   │       ├── subscription.py      ← SubscriptionPlan, Subscription, UsageTracking
│   │       └── audit.py             ← AuditLog, SecurityEvent
│   │
│   ├── schemas/
│   │   ├── auth.py                  ← UserRegister, UserLogin, TokenResponse, MFAVerify
│   │   ├── customer_auth.py         ← ★ NEW: OTPRequest, OTPVerify, CustomerRegister, CustomerToken
│   │   ├── customers.py             ← ★ NEW: CustomerProfile, CustomerUpdate
│   │   ├── reputation.py            ← ReviewCreate, DashboardStats
│   │   ├── loyalty.py               ← ScanRequest, ScanResponse, LoyaltyCard
│   │   └── common.py                ← ApiResponse envelope, ErrorDetail
│   │
│   ├── services/
│   │   ├── auth_service.py          ← Owner/Staff: register, login, mfa, refresh, logout
│   │   ├── customer_otp_service.py  ← ★ NEW: OTP generate, Redis store, verify, rate-check
│   │   ├── customer_service.py      ← ★ NEW: register (encrypted PII), profile, delete
│   │   ├── ai_engine.py             ← GPT-4o / Gemini orchestrator + cache + fallback
│   │   ├── gmb_service.py           ← Google My Business API client
│   │   ├── qr_service.py            ← Generate branch QR codes, upload to R2
│   │   ├── geofence_service.py      ← PostGIS ST_Distance geofence validation
│   │   ├── loyalty_service.py       ← Stamp logging, reward unlocking, scratch cards
│   │   ├── billing_service.py       ← Stripe subscriptions + webhooks
│   │   ├── usage_service.py         ← Track + check plan limits
│   │   ├── messaging_service.py     ← Twilio SMS/WhatsApp + SendGrid
│   │   └── onboarding_service.py    ← Tenant provisioning + RLS setup
│   │
│   ├── workers/
│   │   ├── celery_app.py
│   │   └── tasks.py                 ← sync_gmb, batch_ai, send_rewards, reset_usage, cleanup
│   │
│   ├── middleware/
│   │   └── subdomain.py             ← Host header → tenant_id resolution
│   │
│   ├── templates/                   ← All HTML generated via Stitch.ai + wired with Jinja2
│   │   ├── base.html
│   │   ├── customer/                ← ★ NEW: Stitch.ai-generated customer PWA
│   │   │   ├── otp_login.html
│   │   │   ├── register.html
│   │   │   ├── loyalty_card.html
│   │   │   └── stamp_collected.html
│   │   ├── review_composer.html
│   │   ├── dashboard/
│   │   ├── billing/
│   │   └── emails/
│   │
│   └── main.py                      ← FastAPI app factory
│
├── tests/
│   ├── unit/
│   │   ├── test_customer_otp.py     ← ★ NEW: OTP generation, verification, rate limits
│   │   └── test_customer_service.py ← ★ NEW: Registration, encrypted PII
│   ├── integration/
│   │   └── test_otp_flow.py         ← ★ NEW: Full OTP login + registration E2E
│   └── load/
│       └── locust_otp.py            ← ★ NEW: Load test OTP endpoints
│
├── static/
├── scripts/
│   ├── seed_roles.py
│   ├── seed_plans.py
│   └── rotate_keys.py
├── .github/workflows/
│   ├── ci.yml
│   └── deploy.yml
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
└── requirements/
    ├── base.txt
    ├── dev.txt
    └── prod.txt
```

> ⚠️ **Rule:** Routers handle HTTP only. Services contain all business logic. Models define data only. Stitch.ai exports go into `app/templates/` and are wired via Jinja2 tags.

---

## 3. Database Structure — 4 Data Domains ★ v3.0

> 💡 **v3.0 reorganizes all tables into 4 logical domains, each with its own PostgreSQL schema, access role, and security tier.** This supersedes the flat table list from v2.0. Full field-level detail for every table is in `Doc2_Database_Structure_v3.docx`. Think of each table as an Excel sheet — each row is one record, `FK` means the column points to another table, `★` marks new tables.

### Why Reorganize Into Domains

| Benefit | Why It Matters |
|---------|----------------|
| Blast radius containment | Separate schemas + separate DB roles mean a compromise in one domain doesn't expose the others |
| PCI DSS scope reduction | Tokenization research confirms isolating payment data cuts compliance scope and breach risk significantly — QuickBite never stores a card number, only Stripe tokens |
| Clearer RLS policies | Each domain's policy only reasons about that domain's access pattern |
| Independent caching | Static data can be cached aggressively; Customer and Payment data never cached client-side |
| Faster security audits | Security Dev audits the Payment schema in isolation without touching unrelated tables |

### The 4 Domains

| Domain | Schema | Sensitivity | Tables |
|--------|--------|-------------|--------|
| **1. Restaurant Data** | `restaurant` | Moderate | tenants, users, sessions, branches, google_business_profiles, customer_reviews, review_responses, reward_programs |
| **2. Customer Data** | `customer` | High (PII) | customers, stamp_logs |
| **3. Payment Data ★** | `payment` | **Highest — Most Critical** | subscriptions, usage_tracking, **payment_methods ★**, **billing_events ★**, **invoices ★**, **billing_audit_log ★** |
| **4. Static Data** | `static` | Low (reference only) | roles, subscription_plans, **feature_flags ★**, **notification_templates ★** |

---

### Domain 1 — Restaurant Data
**Schema:** `restaurant` · **Role:** `app_restaurant_rw` · **RLS:** `tenant_id` on every table

Tenant-owned operational data — staff, branches, reviews, GMB connections, reward configuration. Field definitions for `tenants`, `users`, `roles`, `sessions`, `branches`, `google_business_profiles`, `customer_reviews`, `review_responses`, and `reward_programs` are unchanged from v2.0 (see full doc) — only the schema namespace changes, from `public.*` to `restaurant.*`.

### Domain 2 — Customer Data
**Schema:** `customer` · **Role:** `app_customer_rw` · **RLS:** `tenant_id` + customer-scoped encryption

Loyalty member PII. The `customers` table holds AES-256-GCM encrypted phone/email/name with SHA-256 hashed lookup indexes — no plaintext PII is ever queryable. `stamp_logs` is append-only and includes GPS at scan time for fraud audit.

> **Access rule:** only the public OTP and loyalty-scan endpoints write to this schema. Dashboard analytics reads through anonymising views (last-4-digits only) — never the raw `encrypted_phone` column.

### Domain 3 — Payment Data  ★ Most Critical Tier
**Schema:** `payment` · **Role:** `app_payment_rw` (billing service ONLY) · **Network-isolated · Immutable audit log**

The highest-security domain. **Core principle: QuickBite never stores a raw card number, CVV, or expiry date.** Stripe handles all cardholder data and returns only opaque tokens. This mirrors PCI DSS tokenization best practice — replacing sensitive cardholder data with non-sensitive tokens substantially shrinks audit scope and reduces breach risk while keeping the system outside full PCI compliance scope.

**Non-negotiable security rules:**
- Never store a PAN, CVV, or raw expiry date anywhere — not in the DB, not in logs, not in error messages
- Every payment identifier stored is a Stripe-issued token (`stripe_customer_id`, `stripe_payment_method_id`, `stripe_invoice_id`)
- The `payment` schema is reachable **only** from the billing service — no other microservice, dashboard query, or admin route connects directly
- `app_payment_rw` is the only role with grants on this schema; all other roles have it explicitly `REVOKE`d
- Every read/write to `payment.*` is logged to `billing_audit_log` (append-only, stricter retention than general `audit_logs`)
- Stripe webhook signature verified via `construct_event()` **before** any row is touched (SEC-10)
- Defense in depth: even Stripe tokens are AES-256-GCM encrypted at rest in our DB

**New tables in v3.0:**

| Table | Purpose |
|-------|---------|
| `payment_methods` ★ | Stores only the Stripe payment method token + `card_brand`/`card_last4` for display (PCI-permitted, never the full card) |
| `billing_events` ★ | Immutable log of every Stripe webhook event received — append-only, used for reconciliation |
| `invoices` ★ | Read-optimised mirror of Stripe invoices for dashboard display — Stripe remains source of truth |
| `billing_audit_log` ★ | Records every single read/write touching the `payment` schema — which service, which role, which row |

**Recommended physical isolation:**

| Layer | Control |
|-------|---------|
| Schema | `payment` schema separate from the other 3, enforced via PostgreSQL `search_path` and schema-qualified queries |
| DB Role | `app_payment_rw`: `SELECT, INSERT, UPDATE` only — **no `DELETE` grant**, even for the billing service |
| Network | Billing service runs in its own container with the only route to the payment connection string |
| Connection pooling | Separate PgBouncer pool, lower max-connections, stricter idle-timeout |
| Secrets | Payment schema `DATABASE_URL` is a distinct AWS Secrets Manager secret, rotated on its own 90-day schedule |
| Monitoring | SEC-29 alert: any query against `payment.*` from a role other than `app_payment_rw` fires Critical immediately |

### Domain 4 — Static Data
**Schema:** `static` · **Role:** `app_static_ro` (read-only for most services) · Heavily cached, lowest sensitivity

Reference/lookup data that changes rarely and is never tenant-specific or personally identifiable — safe to cache aggressively in Redis or at the CDN edge. `roles` and `subscription_plans` are unchanged from v2.0.

**New tables in v3.0:**

| Table | Purpose |
|-------|---------|
| `feature_flags` ★ | Platform-wide toggles (e.g. primary AI provider, beta rollout percentages). Written only by Super Admin. |
| `notification_templates` ★ | Pre-approved WhatsApp/SMS/email copy referenced by Celery tasks — never hardcoded in task files |

---

### Access Control Matrix

| Service | restaurant | customer | payment | static |
|---------|:----------:|:--------:|:-------:|:------:|
| Public review/loyalty API | RW | RW | **NONE** | RO |
| Customer OTP auth service | RO | RW | **NONE** | RO |
| Owner/Staff auth service | RW | NONE | **NONE** | RO |
| Billing service (isolated) | RO (tenant lookup) | NONE | **RW** | RO |
| Stripe webhook handler | NONE | NONE | **RW** | NONE |
| Dashboard analytics API | RO | RO (anonymised) | RO (status only) | RO |
| Celery background workers | RW | RW | RO | RO |
| Super Admin panel | RW (BYPASSRLS) | RO (anonymised) | RO (status only) | RW |
| Security Dev audit tooling | RO | RO | RO (via `billing_audit_log`) | RO |

### Cross-Domain Relationships

| Relationship | Type | Crosses Domain? |
|-------------|------|------------------|
| `restaurant.tenants` → `restaurant.users` | 1:MANY | No |
| `restaurant.tenants` → `customer.customers` | 1:MANY | ★ Restaurant → Customer |
| `restaurant.tenants` → `payment.subscriptions` | 1:1 | ★ Restaurant → Payment |
| `restaurant.tenants` → `static.subscription_plans` | MANY:1 | ★ Restaurant → Static |
| `customer.customers` → `customer.stamp_logs` | 1:MANY | No |
| `restaurant.branches` → `customer.stamp_logs` | 1:MANY | ★ Restaurant → Customer |
| `payment.subscriptions` → `static.subscription_plans` | MANY:1 | ★ Payment → Static |
| `restaurant.users` → `static.roles` | MANY:1 | ★ Restaurant → Static |

> Cross-domain foreign keys are intentional, but every cross-domain query goes through the application layer (the service owning the destination domain) — never a raw SQL JOIN across schemas from an untrusted caller. This keeps access control enforceable at the database role level.

### Migration Summary

```sql
CREATE SCHEMA restaurant; CREATE SCHEMA customer;
CREATE SCHEMA payment;    CREATE SCHEMA static;

-- Move each existing table to its domain schema
ALTER TABLE tenants SET SCHEMA restaurant;
-- ... repeat per the domain mapping above

-- Create payment domain's 4 new tables
CREATE TABLE payment.payment_methods (...);
CREATE TABLE payment.billing_events (...);
CREATE TABLE payment.invoices (...);
CREATE TABLE payment.billing_audit_log (...);

-- Lock down the payment schema
CREATE ROLE app_payment_rw;
REVOKE ALL ON SCHEMA payment FROM PUBLIC;
GRANT USAGE, SELECT, INSERT, UPDATE ON SCHEMA payment TO app_payment_rw;
```

### New Tickets (add to Doc 5)

| Ticket | Title | Hrs |
|--------|-------|:---:|
| DB-01 | 4-Schema Migration — create schemas, move all 15 tables, update SQLAlchemy models | 6 |
| DB-02 | Payment Domain New Tables — payment_methods, billing_events, invoices, billing_audit_log | 5 |
| DB-03 | Static Domain New Tables — feature_flags, notification_templates | 2 |
| SEC-31 | Payment Schema Isolation Audit — verify only app_payment_rw has grants | 3 |
| SEC-32 | Billing Audit Log Verification — every payment.* access creates a log row | 2 |

> ~18 hours of new work. Recommended timing: DB-01/02/03 during Week 2 alongside TENANT-02 (both touch RLS/migrations); SEC-31/32 alongside the existing SEC-03/04 RLS audit.



## 4. Environment Variables

> 🔴 = Secret — never commit to git, use Secrets Manager  
> 🟢 = Non-secret — safe to document

### Core Application

| Variable | Secret? | Example | Notes |
|---------|---------|---------|-------|
| `SECRET_KEY` | 🔴 YES | random 256-bit hex | Signs Owner/Staff JWTs. Rotate every 90 days. |
| `CUSTOMER_SECRET_KEY` | 🔴 YES | random 256-bit hex | ★ Signs customer loyalty JWTs. Separate from owner secret. |
| `ENVIRONMENT` | 🟢 NO | `local \| staging \| production` | Controls SQL echo, debug mode, Sentry activation |
| `DEBUG` | 🟢 NO | `false` | Never true in production |
| `ALLOWED_HOSTS` | 🟢 NO | `quickbite.ai,*.quickbite.ai` | Prevents host-header injection |

### Database & Cache

| Variable | Secret? | Example | Notes |
|---------|---------|---------|-------|
| `DATABASE_URL` | 🔴 YES | `postgresql+asyncpg://user:pw@host/db` | Must use asyncpg driver |
| `REDIS_URL` | 🔴 YES | `redis://:pass@host:6379/0` | Cache, Celery, rate limits, OTP storage |
| `POSTGIS_ENABLED` | 🟢 NO | `true` | Required for geofencing. Verified on startup. |

### Auth & Security

| Variable | Secret? | Example | Notes |
|---------|---------|---------|-------|
| `JWT_ACCESS_TTL_MINUTES` | 🟢 NO | `15` | Owner/Staff token lifetime |
| `JWT_REFRESH_TTL_DAYS` | 🟢 NO | `30` | Refresh token lifetime. Single-use rotation. |
| `BCRYPT_ROUNDS` | 🟢 NO | `12` | Work factor. ~250ms per hash. |
| `TOTP_ISSUER_NAME` | 🟢 NO | `QuickBite AI` | Shown in Google Authenticator |
| `ENCRYPTION_KEY_V1` | 🔴 YES | base64 32-byte key | AES-256-GCM for all PII. 'V1' enables versioned rotation. |
| `GOOGLE_CLIENT_ID` | 🔴 YES | `123456.apps.googleusercontent.com` | GMB OAuth + Google Sign-In |
| `GOOGLE_CLIENT_SECRET` | 🔴 YES | `GOCSPX-...` | Never expose to frontend |

### ★ Customer OTP Auth

| Variable | Secret? | Example | Notes |
|---------|---------|---------|-------|
| `CUSTOMER_JWT_TTL_DAYS` | 🟢 NO | `7` | Customer loyalty session — longer than owner 15-min |
| `CUSTOMER_OTP_TTL_SECONDS` | 🟢 NO | `300` | OTP valid for 5 minutes. Never exceed 600. |
| `CUSTOMER_OTP_MAX_ATTEMPTS` | 🟢 NO | `3` | OTP invalidated after 3 wrong attempts |
| `CUSTOMER_OTP_RATE_LIMIT_SECONDS` | 🟢 NO | `120` | Min 2 minutes between OTP requests per phone |
| `CUSTOMER_OTP_DAILY_MAX` | 🟢 NO | `5` | Max 5 OTP requests per phone per 24 hours |
| `CUSTOMER_OTP_SMS_TEMPLATE` | 🟢 NO | `Your QuickBite code: {otp}` | SMS body template sent via Twilio |

### ★ Stitch.ai Frontend

| Variable | Secret? | Example | Notes |
|---------|---------|---------|-------|
| `STITCH_AI_API_KEY` | 🔴 YES | `stai_xxxxx` | Stitch.ai API key for component generation |
| `STITCH_AI_DESIGN_SYSTEM` | 🟢 NO | `quickbite_v1` | Saved design system name in Stitch.ai workspace |

### AI Providers

| Variable | Secret? | Notes |
|---------|---------|-------|
| `OPENAI_API_KEY` | 🔴 YES | Primary AI. Set spend limits in OpenAI dashboard. |
| `OPENAI_MODEL` | 🟢 NO | Pin to `gpt-4o`. Prevents surprise from auto-upgrades. |
| `GEMINI_API_KEY` | 🔴 YES | Fallback AI provider |
| `AI_PROVIDER` | 🟢 NO | `auto` = OpenAI with Gemini fallback |

### External Services

| Variable | Secret? | Notes |
|---------|---------|-------|
| `TWILIO_ACCOUNT_SID` | 🔴 YES | SMS + WhatsApp campaigns |
| `TWILIO_AUTH_TOKEN` | 🔴 YES | API auth + webhook signature verification |
| `TWILIO_FROM_NUMBER` | 🟢 NO | Sender phone for SMS |
| `TWILIO_WHATSAPP_FROM` | 🟢 NO | WhatsApp Business number (Pro+ only) |
| `SENDGRID_API_KEY` | 🔴 YES | Email OTP + verification + campaigns |
| `STRIPE_SECRET_KEY` | 🔴 YES | Server-side billing. Never expose to frontend. |
| `STRIPE_WEBHOOK_SECRET` | 🔴 YES | Verifies webhook event signatures |
| `SENTRY_DSN` | 🔴 YES | Error tracking. Staging/prod only. |

### Security Rules — What Must NEVER Be Hardcoded

- ❌ Any key, token, password, or secret — even 'temporary' dev values
- ❌ Database connection strings — even the local dev URL
- ❌ OpenAI or Gemini API keys — even test keys. Use `.env.local` (gitignored)
- ❌ Both `SECRET_KEY` and `CUSTOMER_SECRET_KEY` — never the same value, never committed
- ❌ `BCRYPT_ROUNDS` must not be below 10 in any environment
- ❌ `JWT_ACCESS_TTL_MINUTES` must not exceed 60
- ❌ `CUSTOMER_OTP_TTL_SECONDS` must not exceed 600
- ❌ `ALLOWED_HOSTS` must never contain `*` in production

---

*Document 2 of 6 · QuickBite AI + Loyalty · Technical Architecture v2.0 · Confidential*
