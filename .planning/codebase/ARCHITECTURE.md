<!-- refreshed: 2026-08-19 -->
# Architecture

**Analysis Date:** 2026-08-19

## System Overview

QuickBite is a multi-tenant SaaS for Indian restaurant owners: one QR code on a receipt → customer scans → AI review drafted in <30 seconds + loyalty stamp collected. The architecture enforces three core constraints: **multi-tenant isolation via RLS**, **three authentication families resolving to one identity model**, and **complete separation of business logic (services) from HTTP routing**.

```text
┌──────────────────────────────────────────────────────────────────────┐
│                      FastAPI Application Entry                        │
│                    `app/main.py` — structlog setup                    │
├────────────────────────────────────────────────────────────────────────┤
│              HTTP Request Layer — `/api/v1/` routers                  │
├──────────────┬──────────────┬──────────────┬────────────────┬─────────┤
│  `/auth`     │  `/loyalty`  │ `/reputation`│  `/billing`    │ `/team` │
│ `auth.py`    │ `loyalty.py` │  `reputation│ `billing.py`   │ `team.py`
│              │              │   .py`       │                │         │
└──────────────┴──────────────┴──────────────┴────────────────┴─────────┘
         │                    │                 │              │
         ▼                    ▼                 ▼              ▼
┌──────────────────────────────────────────────────────────────────────┐
│               Dependency Injection Layer                              │
├────────────────────────────────────────────────────────────────────────┤
│  `auth.py`        `customer_auth.py`     `subscription.py`           │
│  • resolve_principal()  • get_current_customer    • tenant_has_feature│
│  • get_current_user()   • optional()              • check_tier()      │
└────────────────────────────────────────────────────────────────────────┘
         │                    │                 │
         ▼                    ▼                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  Service Layer — Business Logic                       │
│                  `app/services/` — NO HTTP, NO DB calls              │
├────────────────────────────────────────────────────────────────────────┤
│ • AuthService           • LoyaltyService      • GMBService           │
│ • CustomerOTPService    • ResponseService    • AIEngine              │
│ • IdentityService       • DashboardService   • BillingService        │
│ • AadminService         • ReviewService      • etc.                  │
└────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│              Database Layer — Async SQLAlchemy + RLS                 │
├────────────────────────────────────────────────────────────────────────┤
│  `app/db/base.py` — AsyncSession factory, engine config              │
│  `app/db/models/` — 13 ORM tables (User, Tenant, Customer, etc.)     │
│  `app/db/rls.py` — set_tenant_context(), tenant isolation            │
│  `app/db/migrations/` — Alembic versions                             │
└────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│         PostgreSQL 16 + PostGIS 3.4 (Supabase)                       │
│  • `restaurant` schema — owner/staff + tenant-scoped tables          │
│  • `customer` schema — loyalty member accounts                       │
│  • `static` schema — reference data (roles, plans)                   │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│              Async Background — Celery + Redis                        │
│                  `app/workers/celery_app.py`                          │
├────────────────────────────────────────────────────────────────────────┤
│  Beat schedule:                                                        │
│  • sync_all_gmb_profiles      (1800s) — REVIEW-03                    │
│  • batch_generate_ai_responses (3600s) — REVIEW-02                   │
│  • drain_projection_outbox    (10s)    — billing → Firestore         │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│              Core Concerns — Globally Shared                          │
│                      `app/core/`                                      │
├────────────────────────────────────────────────────────────────────────┤
│  • config.py         — Pydantic BaseSettings (env vars)              │
│  • security.py       — JWT (HS256), bcrypt, TOTP                    │
│  • customer_security.py  — customer JWT (HS256, different key)       │
│  • principal.py      — authenticated identity model                  │
│  • rbac.py          — role hierarchy + invitable roles               │
│  • encryption.py     — AES-256-GCM for PII                           │
│  • rate_limiter.py   — SlowAPI on all public endpoints               │
│  • cache_service.py  — Redis client (OTP, drafts, etc.)              │
│  • firebase_auth.py  — Firebase ID token verification                │
│  • supabase_auth.py  — Supabase JWT verification (JWKS)              │
│  • email_transport.py — SMTP / SendGrid                              │
│  • firestore_client.py — Firestore for billing projection            │
└──────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **FastAPI App** | HTTP request routing, middleware registration, startup validation | `app/main.py` |
| **Auth Router** | POST /identify, /register, /login, /mfa/*, /refresh, /logout, /me | `app/api/v1/routers/auth.py` |
| **Loyalty Router** | POST /scan, /reward-programs, POST /redeem/{code}, GET /analytics | `app/api/v1/routers/loyalty.py` |
| **Reputation Router** | GET /reviews, POST /responses, webhooks for GMB sync | `app/api/v1/routers/reputation.py` |
| **Billing Router** | POST /checkout, GET /portal, webhooks for Razorpay | `app/api/v1/routers/billing.py` |
| **Team Router** | GET /members, POST /invite, DELETE /members/{id} | `app/api/v1/routers/team.py` |
| **resolve_principal()** | Classify token family, verify signature, load ORM row, bind tenant context | `app/api/v1/dependencies/auth.py` |
| **get_current_user()** | Assert USER subject type, bind RLS tenant context, set request.state | `app/api/v1/dependencies/auth.py` |
| **require_role()** | Check user.role.level against required level, return 403 if insufficient | `app/api/v1/dependencies/auth.py` |
| **AuthService** | Registration, email verification, login, MFA enrollment, token refresh, logout | `app/services/auth_service.py` |
| **LoyaltyService** | Scan validation, stamp logging, geofence check, reward program CRUD | `app/services/loyalty_service.py` |
| **AIEngine** | OpenAI/Gemini fallback wrapper, caching, timeout enforcement | `app/services/ai_engine.py` |
| **ResponseService** | AI review composition, plagiarism check, sentiment analysis | `app/services/response_service.py` |
| **GMBService** | OAuth flow, review sync, response posting, batch operations | `app/services/gmb_service.py` |
| **BillingService** | Checkout creation, subscription state tracking, plan enforcement | `app/services/billing_service.py` |
| **IdentityLinkService** | Map external tokens (Supabase/Firebase) to local User/Customer rows | `app/services/identity_link_service.py` |
| **DashboardService** | Aggregate KPIs: reviews, stamps, revenue, churn | `app/services/dashboard_service.py` |
| **User Model** | Owner/Manager/Staff account (email + password or external auth) | `app/db/models/user.py` |
| **Customer Model** | Loyalty member (phone/email + OTP or external auth, stamps, preferences) | `app/db/models/customer.py` |
| **Tenant Model** | Restaurant root entity, subscription plan, business registration (GSTIN/PAN) | `app/db/models/tenant.py` |
| **Branch Model** | Location with PostGIS geometry, geofence radius, QR token | `app/db/models/branch.py` |
| **Base Model** | Async SQLAlchemy engine, session factory, UUID PK + timestamps | `app/db/base.py` |
| **RLS Module** | set_tenant_context(), admin_bypass_context(), transaction-local isolation | `app/db/rls.py` |
| **Celery App** | Redis broker config, Beat schedule for sync/draft/billing tasks | `app/workers/celery_app.py` |
| **Celery Tasks** | sync_all_gmb_profiles, batch_generate_ai_responses, drain_projection_outbox | `app/workers/tasks.py` |

## Pattern Overview

**Overall:** Three-layer clean architecture (HTTP → Services → DB) with multi-tenant isolation enforced at the database layer (RLS), authentication identity abstraction (three token families → one Principal), and async-first async/await throughout.

**Key Characteristics:**
- **No business logic in routes.** Every endpoint is thin: extract+validate input, call service, return response. Zero SQL queries in routers.
- **Schema-first development.** Pydantic schemas defined in `app/schemas/` before routes, enforcing type safety at the boundary.
- **Multi-tenant by design.** Every table in `restaurant` schema carries a `tenant_id` column, every query includes a `WHERE tenant_id = current_setting('app.tenant_id')::uuid` RLS policy. Tenant context set per-request by `resolve_principal()`.
- **Three auth families, one identity.** Local HS256 (built-in), Supabase ES256/RS256 (JWKS cache), Firebase RS256 (Google certs). All resolve to `Principal(user=User | customer=Customer)` so downstream code is provider-blind.
- **RBAC by rank.** Four staff roles (SUPER_ADMIN=1, OWNER=2, MANAGER=3, STAFF=4) + standard USER=6 (tenant-less, email verified but no restaurant) + loyalty CUSTOMER=5. `require_role(RoleLevel.MANAGER)` checks `user.role.level`.
- **PII encryption by tier.** TIER 1 (plaintext safe data like role names, status flags), TIER 2 (SHA-256 hash only for indexed lookups), TIER 3 (hash + AES-256-GCM for searchable and displayable data). All phone/email/address/business numbers follow this.
- **Async-first.** Every model uses `AsyncSession`, every external call is awaited (no `asyncio.run()`, no `time.sleep()`, no blocking imports at module scope).
- **Idempotent tasks.** All Celery tasks use `bind=True`, `max_retries=3`, accept only JSON-serializable args, set idempotency keys in Redis to prevent duplicate side effects.
- **Structured logging.** structlog JSON output, every log includes context (tenant_id, user_id, operation), no print() or logging.basicConfig().
- **Rate limiting everywhere.** SlowAPI `@limiter.limit()` on every public endpoint. OTP endpoints have aggressive limits (5/day, 3/5min). Scan endpoints have geofence + per-IP limits.

## Layers

**HTTP API Layer:**
- Purpose: Parse HTTP requests, apply rate limits, invoke services, return JSON responses
- Location: `app/api/v1/routers/`
- Contains: One file per domain (auth.py, loyalty.py, reputation.py, billing.py, team.py, admin.py, customers.py, dashboard.py, pages.py for server-rendered)
- Depends on: Services, schemas, dependencies (auth/subscription)
- Used by: External clients, browsers (for Jinja2 templates)
- Pattern: `async def endpoint(...) -> ResponseSchema` with minimal logic

**Dependency Injection Layer:**
- Purpose: Resolve authenticated principals, enforce role-based access, check subscription tiers
- Location: `app/api/v1/dependencies/`
- Contains: `auth.py` (resolve_principal, get_current_user, require_role), `customer_auth.py` (get_current_customer_optional), `subscription.py` (tenant_has_feature)
- Depends on: Principals, RLS, cache
- Used by: Route handlers via `Depends()` parameter
- Pattern: `async def get_current_user(...) -> Principal` → bind RLS → load row → return

**Service Layer:**
- Purpose: Encapsulate business logic, database access, external API calls
- Location: `app/services/`
- Contains: One class per business domain (AuthService, LoyaltyService, GMBService, etc.)
- Depends on: Models, schemas, external clients, other services (messaging_service for notifications)
- Used by: Route handlers, other services, Celery tasks
- Pattern: Class methods accept primitive types/ORM objects, return domain DTOs (Pydantic schemas)
- **Critical rule:** Services are instantiated per-request with `service = LoyaltyService(session=session, tenant_id=principal.tenant_id)`, never module-level singletons (exception: `cache_service` and `broadcast` which are async-safe clients)

**Database Models Layer:**
- Purpose: Define ORM schema, enforce constraints, express relationships
- Location: `app/db/models/`
- Contains: 13 models: User (staff), Customer (loyalty), Tenant (root), Branch (location), Session, Role, IdentityLink, Subscription, Audit, Review, Response, Loyalty (stamps/rewards), Reputation, Outbox
- Depends on: Base (UUID PK, timestamps)
- Used by: Services, migration code
- Pattern: Inherit from `Base`, map columns to Python types, use ForeignKey for relationships

**Data Access & RLS Layer:**
- Purpose: Manage async connections, enforce row-level security
- Location: `app/db/base.py` and `app/db/rls.py`
- Contains: `async_session_factory`, `get_db()` FastAPI dependency, `set_tenant_context()`, `admin_bypass_context()`
- Depends on: PostgreSQL asyncpg driver, Alembic migrations
- Used by: Services, routes (via Depends), tasks, webhooks
- Pattern: `async with async_session_factory() as session:` then call services; tenant context is transaction-local so it survives `session.commit()`

**Core Concerns Layer:**
- Purpose: Shared cryptography, configuration, identity models, cross-cutting concerns
- Location: `app/core/`
- Contains: `config.py` (Settings), `security.py` (JWT/bcrypt/TOTP), `principal.py` (Principal dataclass), `rbac.py` (RoleLevel), `encryption.py` (AES-256-GCM), `rate_limiter.py` (SlowAPI limiter), `cache_service.py` (Redis async client), auth verifiers (firebase_auth.py, supabase_auth.py), external clients (email_transport.py, firestore_client.py)
- Depends on: External SDKs (PyJWT, passlib, pyotp, cryptography, aioredis, firebase-admin, etc.)
- Used by: Every other layer
- Pattern: Module-level singletons that are async-safe (e.g., cache_service.get(), cache_service.set())

**Workers & Async Tasks:**
- Purpose: Run long-running jobs asynchronously, scheduled via Celery Beat
- Location: `app/workers/`
- Contains: `celery_app.py` (Celery + Redis config, Beat schedule), `tasks.py` (three scheduled tasks)
- Depends on: Services, database session, cache
- Used by: Celery beat scheduler (internal), explicit task.delay() calls (rare)
- Pattern: `@celery_app.task(bind=True, max_retries=3) def task_name(self, arg1: str, ...)` with idempotency key check

## Data Flow

### Primary Request Path (Owner Login → Access Dashboard)

1. **HTTP POST /api/v1/auth/identify** (`app/api/v1/routers/auth.py:59`) → payload.email + payload.password
2. **IdentifyResponse** includes `identifies_as` enum (STAFF / CUSTOMER / UNKNOWN)
3. **HTTP POST /api/v1/auth/login** (if STAFF) with email + password + optional mfa_code
4. **AuthService.login()** (`app/services/auth_service.py`)
   - Query `User` by `email_hash` (TIER 2 lookup)
   - Verify password against `hashed_password` (bcrypt)
   - If MFA enabled: verify `totp_code` against `totp_secret` (RFC 6238)
   - Create `Session` record with refresh token hash, IP hash, user agent
   - Mint `access_token` (HS256, 15min TTL) + `refresh_token` (opaque, 30d TTL)
5. **Client caches access_token in memory** (never localStorage, XSS risk)
6. **HTTP GET /api/v1/auth/me** with `Authorization: Bearer <access_token>`
7. **resolve_principal()** (`app/api/v1/dependencies/auth.py:100`)
   - Extract Bearer token
   - Classify provider (LOCAL → HS256, issuer=null) → route to `_resolve_local()`
   - Decode HS256 with SECRET_KEY, verify `jti` not in revocation blocklist
   - Bind `tenant_id` to `app.tenant_id` GUC via `rls.set_tenant_context()`
   - Query `User` by `user_id` from claims — RLS policy filters to authenticated tenant
   - Load `role` relationship → check `role.mfa_required`
   - Return `Principal(subject_type=USER, user=User, tenant_id=...)`
8. **get_current_user()** (`app/api/v1/dependencies/auth.py`) wraps resolve_principal, checks `is_active`
9. **MeResponse** includes `role`, `permissions`, `tenant`, current plan limits
10. **Client accesses /api/v1/dashboard** — route checks `require_role(RoleLevel.MANAGER)` in dependency
11. **DashboardService.get_dashboard_metrics()** aggregates:
    - Reviews: count, avg rating (last 7d)
    - Stamps: count (last 7d), redemptions
    - Customers: new, returning, churn
    - Plans: current tier, feature usage
    - All scoped to `tenant_id` via RLS
12. **HTTP 200** with analytics JSON

### Secondary Flow: Customer QR Scan & Loyalty Stamp

1. **QR Code on receipt encodes:** `qr_token=<Branch.qr_token>` + timestamp
2. **Mobile browser POST /api/v1/loyalty/scan**
   ```json
   {"qr_token": "abc...", "gps_lat": 28.7041, "gps_lng": 77.1025}
   ```
3. **LoyaltyService.process_scan()** (`app/services/loyalty_service.py:50`)
   - Query `Branch` by `qr_token` (index lookup, ~1ms)
   - Calculate distance via PostGIS: `ST_DWithin(Branch.location, Point(lng,lat), radius_meters)`
   - If outside geofence: log fraud attempt, return HTTP 200 with `{stamp_count: 0, rate_limit_exceeded: false}` (no reveal)
   - If inside geofence: 
     - Check rate limit (Redis key `stamp:{branch_id}:{customer_phone_hash}`, 1 per hour)
     - Increment `StampLog` count
     - Calculate `current_reward_count` from `RewardProgram.stamps_per_reward`
     - If unlocked: generate `redemption_code`, increment `current_reward_count`, broadcast notification
     - Decrement `current_reward_count` on redemption
4. **HTTP 200** with `ScanResponse(stamp_count: int, reward_progress: 0-100, reward_unlocked: bool, redemption_code: str | null)`
5. **If customer authenticated:** populate `ReviewDraft` row linking `customer_id` + `branch_id`
6. **Celery task `send_reward_notification`** (idempotent key set in Redis for 24h) sends WhatsApp/SMS

### Billing Webhook Path (Razorpay Payment Success)

1. **POST /api/v1/webhooks/razorpay** with X-Razorpay-Signature header
2. **BillingService.verify_razorpay_signature()** — HMAC SHA-256 against `RAZORPAY_WEBHOOK_SECRET`
3. **Parse event:** `event_type = payment.captured`, `order_id = checkout.order_id`
4. **Query Checkout by order_id** — *no tenant context bound* (webhook is unauthenticated)
5. **BillingService.handle_payment_success()** inside `rls.tenant_context(checkout.tenant_id)`
   - Create `Subscription` record with `plan_id`, `starts_at`, `renews_at`
   - Set `Tenant.plan_id` to active plan
   - Write `payment.projection_outbox` row (for Firestore mirror)
   - Broadcast `billing.subscription_activated` (if not first plan, sends email via queue)
6. **Celery task `drain_projection_outbox`** (Beat schedule 10s) reads `Outbox` rows, posts to Firestore, deletes row
7. **Stripe/Razorpay dashboard** shows payment settled ✓

**State Management:**
- **Session state:** `Session` table stores refresh token hash (to revoke all devices), `User.tokens_valid_from` is watermark for token revocation
- **Customer loyalty state:** `Customer.total_stamps_alltime`, `current_reward_count`, `StampLog` rows for fraud/analytics
- **Subscription state:** `Subscription` table holds plan + renewal date; `Tenant.plan_id` is current active
- **AI draft cache:** Redis key `ai_draft:{review_id}`, 3600s TTL (REVIEW-01 <3s requirement)
- **OTP cache:** Redis key `otp:{tenant_id}:{phone_hash}`, SHA-256(otp), 300s TTL, 5/day max, 3/5min attempts
- **Rate limit cache:** Redis key `stamp:{branch_id}:{customer_hash}`, 3600s TTL (1 per hour per location)
- **Revocation cache:** Redis key `revoked_jti:{jti}`, 30d TTL (matches refresh token lifetime)

## Key Abstractions

**Principal:**
- Purpose: One object representing an authenticated caller, provider-blind
- Examples: `app/core/principal.py`
- Pattern: Frozen dataclass with `subject_type` (USER | CUSTOMER), `auth_provider` (LOCAL | SUPABASE | FIREBASE), `claims` dict (decoded JWT), `user` or `customer` ORM row. `tenant_id` always from row, never claim.

**AuthProvider:**
- Purpose: Distinguish token families for verification, encryption, revocation
- Examples: `LOCAL` (HS256, SECRET_KEY), `SUPABASE` (ES256/RS256, JWKS), `FIREBASE` (RS256, Google certs)
- Pattern: Enum selects verifier and key source; algorithm confusion attacks prevented by per-provider algorithm allowlist

**RoleLevel:**
- Purpose: Numeric rank for permission checks
- Examples: SUPER_ADMIN=1, OWNER=2, MANAGER=3, STAFF=4, CUSTOMER=5, USER=6
- Pattern: `require_role(RoleLevel.MANAGER)` compares `user.role.level` and rejects if greater (lower rank = more senior)

**IdentityLink:**
- Purpose: Map external tokens (Supabase/Firebase `sub` claim) to local `User` or `Customer` row
- Examples: `app/db/models/identity_link.py`
- Pattern: One row per external identity, stores `provider`, `provider_subject`, `subject_type`, `local_id`. Lookup during external token verification.

**LoyaltyService & StampLog:**
- Purpose: Encapsulate geofence logic, rate limiting, reward calculation
- Examples: `app/services/loyalty_service.py`, `app/db/models/loyalty.py`
- Pattern: Service method takes route request (qr_token, gps), returns response schema. StampLog captures every attempt (valid and fraud) for audit.

**ResponseService (AI Composition):**
- Purpose: Apply safety checks, sentiment analysis, plagiarism detection to AI-drafted reviews
- Examples: `app/services/response_service.py`
- Pattern: Service wraps AIEngine output, returns `ResponseSchema(rating, sentiment, draft_text, is_safe)`

**BillingService & Subscription:**
- Purpose: Manage checkout flow, track renewal dates, check plan tier for feature gates
- Examples: `app/services/billing_service.py`, `app/db/models/subscription.py`
- Pattern: Service methods: `create_checkout()`, `handle_payment_success()`, `get_active_subscription()`. Subscription table stores `plan_id`, `starts_at`, `renews_at`, `status`.

## Entry Points

**FastAPI Application:**
- Location: `app/main.py`
- Triggers: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- Responsibilities: Configure structlog, include all routers, register exception handlers (SlowAPI), mount static files, validate PostGIS on startup

**Authentication Routes:**
- Location: `app/api/v1/routers/auth.py`
- Triggers: POST /api/v1/auth/identify (entry-first login), POST /api/v1/auth/login, POST /api/v1/auth/register
- Responsibilities: Route requests to AuthService, return TokenResponse with access/refresh tokens

**Customer OTP Routes:**
- Location: `app/api/v1/routers/customer_auth.py`
- Triggers: POST /api/v1/customer/request-otp, POST /api/v1/customer/verify-otp
- Responsibilities: Rate-limited OTP generation/verification, return loyalty customer JWT

**Loyalty Scan Route:**
- Location: `app/api/v1/routers/loyalty.py:42`
- Triggers: POST /api/v1/loyalty/scan with geofence data
- Responsibilities: Invoke LoyaltyService, validate geofence, log stamp, return updated loyalty status

**Razorpay Webhook:**
- Location: `app/api/v1/routers/billing.py` (billing_webhook_router)
- Triggers: Razorpay sends POST to /api/v1/webhooks/razorpay on payment_captured
- Responsibilities: Verify HMAC signature, call BillingService.handle_payment_success(), create subscription

**Celery Beat Schedule:**
- Location: `app/workers/celery_app.py`
- Triggers: Runs on intervals defined in beat_schedule dict
  - `sync_all_gmb_profiles` every 30min (REVIEW-03)
  - `batch_generate_ai_responses` every 60min (REVIEW-02)
  - `drain_projection_outbox` every 10s (billing)
- Responsibilities: Spawn task workers via Redis broker

**Server-Rendered Pages:**
- Location: `app/api/v1/routers/pages.py`
- Triggers: GET /verify-email (email verification link), GET /login (login form), GET / (landing page)
- Responsibilities: Render Jinja2 templates with context

## Architectural Constraints

- **Threading:** Single-threaded event loop (FastAPI + asyncio). No background threads. Celery workers run in separate processes.
- **Global state:** Intentionally minimized. Singletons allowed: `cache_service` (async-safe Redis client), `limiter` (SlowAPI state), `settings` (immutable Pydantic model). Every service instantiated per-request, never module-level.
- **Circular imports:** No circular imports permitted. Structure: `api/routers/ → services → models → db/base`. Avoided by importing at function scope in a few places (identity_link_service).
- **Transaction isolation:** Default `READ_COMMITTED` on PostgreSQL. RLS policies run inside transaction, so a tenant-scoped transaction cannot see other tenants' data even if the policy fails.
- **Async safety:** All database calls use `await`, no blocking imports. Libraries checked for async/await compatibility (httpx for AI requests, aioredis for cache, etc.).
- **Multi-tenancy:** No query can run without tenant context bound (RLS enforces `tenant_id = current_setting(...)::uuid`). An unscoped query returns zero rows, not an error. This is intentional — a missing tenant context is safer than a data leak.
- **Prepared statements:** Disabled on transaction pooler (Supabase port 6543) because the pooler recycles connections between transactions. Raw SQL queries must use parameterized queries (`:param` syntax), never f-strings.
- **Refresh token storage:** Hash only (`refresh_token_hash`), never the plaintext token. Prevents an attacker who reads the database from rotating sessions without cracking the hash.
- **PII in logs:** Structlog output never includes plaintext PII. All logs include `tenant_id` for audit, but phone/email/names are omitted.

## Anti-Patterns

### Storing PII Plaintext

**What happens:** A developer adds `user.phone = phone` to the User model, storing the raw phone number in a column.

**Why it's wrong:** 
- Violates compliance (GDPR, India's DPDP Act 2023)
- Creates a liability if the database is compromised
- Makes migration/deletion extremely difficult (must search/anonymise everywhere)

**Do this instead:** 
- Tier 2 (lookup only): `phone_hash = sha256_hex(phone)`, store only the hash in a `phone_hash` column, use it for lookups
- Tier 3 (lookup + display): `phone_hash = sha256_hex(phone)` + `encrypted_phone = encrypt_pii(phone, ENCRYPTION_KEY_V1)`, store both, decrypt only when displaying

File references: `app/db/models/user.py`, `app/db/models/customer.py`, `app/core/encryption.py`

### Trusting JWT Claims for Authorization

**What happens:** A route checks `if "manager" in claims.get("app_metadata", {}).get("roles", [])` to decide if a user can manage a restaurant.

**Why it's wrong:**
- External identity providers (Supabase, Firebase) let users or admins modify their own metadata — a user could claim any role
- Even if the provider is trusted, the claim comes from a different system; our database is the source of truth
- Role changes (promotion, demotion, removal) cannot take effect immediately across all active sessions

**Do this instead:**
- Let the token identify the user, never authorize based on claims alone
- Load the `User` row from `restaurant.users` table and read `user.role_id` (follows `role.level`)
- `require_role()` compares `user.role.level` against the threshold, not against claims

File references: `app/api/v1/dependencies/auth.py:142` (_assert_not_globally_revoked), `app/core/rbac.py`

### Skipping RLS Context Binding

**What happens:** A Celery task runs a query on an RLS table without calling `rls.set_tenant_context()`.

**Why it's wrong:**
- The query returns **zero rows** (RLS filters, not errors), silently failing
- No exception is raised, so the bug is invisible for hours/days until logs show missing data
- A task that silently does nothing is far worse than one that crashes

**Do this instead:**
- Wrap tenant-scoped queries inside `async with rls.tenant_context(session, tenant_id):` 
- For routes: call `set_tenant_context()` inside resolve_principal before querying User
- For webhooks: wrap the handler inside tenant_context (Razorpay example in billing_service.py)

File references: `app/db/rls.py`, `app/api/v1/dependencies/auth.py:210`, `app/services/billing_service.py`

### Blocking Calls in Async Context

**What happens:** A route calls `time.sleep(1)` to rate-limit a retry, or imports a blocking library at module scope.

**Why it's wrong:**
- Blocks the entire event loop, freezing all other requests on that worker
- 10 concurrent requests each sleeping 1s = 10s total latency for all of them
- Defeats the entire purpose of async/await

**Do this instead:**
- Use `await asyncio.sleep()` for delays
- Use `SlowAPI` for rate limiting, not manual sleeps
- Avoid blocking imports: not `requests`, but `httpx`; not `redis`, but `aioredis`

File references: `app/workers/tasks.py`, `app/services/ai_engine.py`

### Unvalidated External Inputs in SQL

**What happens:** A route builds SQL with f-strings: `query = f"SELECT * FROM reviews WHERE id = {review_id}"`

**Why it's wrong:**
- SQL injection: `review_id = "1 OR 1=1"` would bypass the WHERE clause
- Parameterization is free in SQLAlchemy — no performance cost

**Do this instead:**
- Always use parameterized queries: `select(Review).where(Review.id == review_id)` 
- SQLAlchemy handles escaping automatically
- For raw SQL (rare): `session.execute(text("...WHERE id = :id"), {"id": review_id})`

File references: `app/db/base.py` (all models use SQLAlchemy ORM, never raw SQL)

## Error Handling

**Strategy:** HTTP exceptions with structured JSON body, every error includes a machine-readable `code` and a `message`, rate-limit errors include `retry_after_seconds` header.

**Patterns:**
- **400 Bad Request:** Pydantic validation failure (automatic via FastAPI)
- **401 Unauthorized:** Invalid/expired token, deactivated account
- **402 Payment Required:** Plan limit hit (e.g., too many branches for free tier)
- **403 Forbidden:** Insufficient role (not MANAGER when MANAGER required), account deactivated
- **404 Not Found:** Resource doesn't exist (NEVER for unregistered phone — returns 200 with `identified_as: UNKNOWN`)
- **409 Conflict:** Duplicate email/phone, existing restaurant with that subdomain
- **429 Too Many Requests:** Rate limit hit (SlowAPI), always includes `Retry-After` header (seconds)
- **503 Service Unavailable:** External API unreachable (OpenAI timeout, Twilio down)

Example error response:
```json
{
  "error": {
    "code": "OTP_RATE_LIMIT_EXCEEDED",
    "message": "You have exhausted your daily OTP limit. Please try again tomorrow.",
    "retry_after_seconds": 86400
  }
}
```

File references: `app/api/v1/dependencies/auth.py:46-64` (error constants), `app/services/auth_service.py` (raises HTTPException)

## Cross-Cutting Concerns

**Logging:** structlog JSON output, every log includes `event`, `tenant_id`, `user_id` (if authenticated), `error` (if exception), timestamps in ISO 8601. Structured so log aggregation systems can parse and alert on error thresholds.

File reference: `app/main.py:19-32` (structlog configuration)

**Validation:** Pydantic `BaseModel` in schemas layer enforces type, range, regex at the boundary. No validation logic in services (assume input is clean). SQLAlchemy constraints (UNIQUE, NOT NULL, FK) enforce at database.

File references: `app/schemas/`, `app/db/models/`

**Authentication:** Three-way dispatch: local HS256 → `_resolve_local()`, Supabase ES256/RS256 → `_resolve_supabase()`, Firebase RS256 → `_resolve_firebase()`. All return same `Principal` shape. Routes stay provider-blind.

File reference: `app/api/v1/dependencies/auth.py:100`

**Authorization:** Role-based access control, hierarchy enforced: `require_role(RoleLevel.MANAGER)` rejects STAFF or USER. Features gated by subscription tier: `tenant_has_feature("whatsapp")` checks active plan's feature_limits.

File references: `app/core/rbac.py`, `app/api/v1/dependencies/subscription.py`

**Rate Limiting:** SlowAPI on every public endpoint (auth, OTP, scan, etc.). Keys: per-IP (scans), per-user (login), per-phone (OTP). Configured in route decorators.

File reference: `app/core/rate_limiter.py`, routes decorated with `@limiter.limit("10/minute")`

**Encryption:** AES-256-GCM with random IV per call, used for TIER 3 PII (phone, email, address). Key rotated via `ENCRYPTION_KEY_V1` env var. Decryption only on display, never in queries. SHA-256 hash used for searchable index.

File reference: `app/core/encryption.py`

---

*Architecture analysis: 2026-08-19*
