# External Integrations

**Analysis Date:** 2026-08-19

## APIs & External Services

**AI Review Generation:**
- OpenAI GPT-4o (`openai` SDK)
  - What: AI-generated review responses under 3 seconds (REVIEW-01 criterion)
  - Config: `OPENAI_API_KEY`, `OPENAI_MODEL` (default: gpt-4o)
  - Timeout: 1.2s per provider (3s total with fallback to Gemini)
  - Service: `app/services/ai_engine.py`
  - Cache: 1h Redis cache for drafts

- Google Gemini 1.5 Pro (`google-generativeai` SDK)
  - What: Fallback when OpenAI timeout/fails or disabled via feature flag
  - Config: `GEMINI_API_KEY`, `GEMINI_MODEL`
  - Timeout: 1.2s per provider
  - Service: `app/services/ai_engine.py`
  - Feature flag: `AI_PROVIDER` env var (auto|openai|gemini)

**SMS & WhatsApp:**
- Twilio
  - What: SMS OTP delivery for customer login; WhatsApp for Pro+ plan upgrades
  - SDK: `twilio` client
  - Auth: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
  - Numbers: `TWILIO_FROM_NUMBER` (SMS), `TWILIO_WHATSAPP_FROM` (WhatsApp)
  - Service: `app/services/messaging_service.py`, `app/services/customer_otp_service.py`
  - Rate limit: 1 OTP per 2 minutes, max 5 per day per customer

**Email:**
- SMTP (aiosmtplib) — Primary
  - What: Async email for signup OTP, password reset, invitations
  - Config: `SMTP_HOST` (default: smtp.gmail.com), `SMTP_PORT` (587), `SMTP_USERNAME`, `SMTP_PASSWORD`
  - Security: `SMTP_SECURITY` (starttls|tls|none)
  - From: `EMAIL_FROM_ADDRESS`, `EMAIL_FROM_NAME`
  - Service: `app/core/email_transport.py`
  - Note: Never blocks event loop (async only)

- SendGrid — Fallback
  - What: Email API alternative when `EMAIL_PROVIDER=sendgrid`
  - SDK: `sendgrid` client
  - Auth: `SENDGRID_API_KEY`
  - From: `EMAIL_FROM_ADDRESS`

**Billing & Payment:**
- Razorpay (India-first)
  - What: Subscription orders, renewals, refunds (BILLING-01+)
  - SDK: `razorpay` client (orders/subscriptions API only)
  - Auth: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`
  - Webhook Secret: `RAZORPAY_WEBHOOK_SECRET` (HMAC-SHA256 verification)
  - Webhook signature verification: Local `hmac.compare_digest()`, NOT via SDK
  - Service: `app/services/billing_service.py`
  - Route: `app/api/v1/routers/billing.py` — `/webhook/razorpay` endpoint
  - Pricing: All amounts in INR paise (divide by 100 for display)
  - Tenants: Carry GSTIN/PAN for taxation

**Authentication & Identity:**
- Firebase Auth
  - What: Identity verification + social sign-in (Google, Apple, Microsoft) via web SDK
  - SDK: `firebase-admin` (server-side token verification)
  - Credentials: `FIREBASE_PROJECT_ID`, `FIREBASE_SERVICE_ACCOUNT_JSON` (inline JSON)
  - Issuer: `https://securetoken.google.com/{project_id}`
  - Token verification: `firebase_auth.py` — RSA signature validation
  - JWKS caching: 10 minutes (per Firebase's key rotation window)
  - Web config (client-side): `FIREBASE_WEB_API_KEY`, `FIREBASE_WEB_AUTH_DOMAIN`, `FIREBASE_WEB_APP_ID`
  - Service: `app/core/firebase_auth.py`
  - Firestore projection: `app/core/firestore_client.py` — Mirror user metadata to Firestore

- Supabase Auth
  - What: Alternative identity provider (email OTP, phone OTP, social)
  - SDK: JWT verification via JWKS (no SDK dependency)
  - Credentials: `SUPABASE_PROJECT_REF`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
  - Issuer: `https://{ref}.supabase.co/auth/v1`
  - JWKS endpoint: `{issuer}/.well-known/jwks.json`
  - JWKS caching: 10 minutes
  - Status: Optional (only routes token if `SUPABASE_PROJECT_REF` configured)
  - Service: `app/core/supabase_auth.py`

- Google OAuth (for GMB access)
  - What: Restaurant owner grants QuickBite permission to sync reviews from Google My Business
  - Flow: Authorization code → refresh token storage
  - Redirect URI: `GMB_OAUTH_REDIRECT_URI` (must be registered on Google Cloud Console)
  - Service: `app/services/gmb_service.py`
  - Ticket: REVIEW-03

**Google My Business:**
- Google My Business API (v4.9)
  - What: Fetch new reviews from GMB profile, post AI-drafted responses
  - Auth: OAuth 2.0 refresh token (stored per tenant in DB)
  - Sync schedule: Every 30 minutes via Celery beat task
  - Service: `app/services/gmb_service.py`, `app/services/review_sync_service.py`
  - Tasks: `app/workers/tasks.py` — `sync_all_gmb_profiles`, `batch_generate_ai_responses`
  - Ticket: REVIEW-03

**Geolocation & Maps:**
- PostGIS 3.4 (PostgreSQL extension)
  - What: Geofence scanning for loyalty stamp collection (LOYALTY-03)
  - SQL: `ST_DWithin(branch.location, customer_point, radius_m)` for radius queries
  - Coordinates: WGS84 (SRID 4326), stored as POINT geometry
  - Service: `app/services/geofence_service.py`
  - Queries: GeoAlchemy2 `geography` type for meter-accurate distances

## Data Storage

**Databases:**
- PostgreSQL 16
  - Connection: `DATABASE_URL` (async via asyncpg)
  - Pool: Supabase transaction pooler (port 6543) or direct (5432)
  - RLS: Row-level security enforced via `app.tenant_id` context var
  - Extensions: PostGIS 3.4 (required for geofencing)
  - Tables: 13 domain models in `app/db/models/` (tenant, branch, user, customer, reputation, loyalty, payment, subscription, audit, identity_link, static_data, outbox)
  - Migrations: Alembic in `migrations/versions/` (one per schema change)

- Firestore (Google Cloud)
  - What: User projection mirror for Dashboard real-time data
  - Service account: `firebase-admin` (requires `FIREBASE_SERVICE_ACCOUNT_JSON`)
  - Database: `(default)` or custom via `FIRESTORE_DATABASE` env var
  - Projection: `app/services/projection_service.py` — Outbox pattern drains to Firestore every 10s
  - Use: Dashboard stats, Analytics (DASHBOARD-01+)
  - Write-through: On every billing/loyalty event, write to `projection_outbox` table; Celery beat drains it

**File Storage:**
- Cloudflare R2 (S3-compatible object storage)
  - What: QR code PNGs, receipt exports, customer avatars, logos
  - SDK: Boto3 (S3 API)
  - Credentials: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`
  - Service: `app/services/qr_service.py` (QR code generation)
  - Public URLs: Configured via Cloudflare CDN

**Caching:**
- Redis 7
  - Connection: `REDIS_URL` (default: `redis://localhost:6379/0`)
  - Use cases:
    - Celery broker (task queue)
    - Celery result backend (task results)
    - OTP storage (SHA-256 hashed, 5-minute TTL)
    - AI draft cache (1-hour TTL per REVIEW-01)
    - JWKS cache (10-minute TTL for Firebase/Supabase keys)
    - Feature flags
    - Session tokens
  - Service: `app/core/cache_service.py`

## Authentication & Identity

**Auth Providers:**
- Firebase Auth (primary, web SDK)
  - Token type: ID token (RS256 asymmetric signature)
  - Flow: Browser-based social sign-in → ID token → Server-side verification
  - Verification: `firebase_auth.py` — fetch JWKS, validate signature, check `aud`/`iss`

- Supabase Auth (fallback)
  - Token type: JWT (ES256 asymmetric, or HS256 depending on Supabase config)
  - Flow: Email/phone OTP → token → Server-side verification
  - Verification: `supabase_auth.py` — fetch JWKS, validate signature

**JWT Security:**
```python
# Owners/staff use one key (HS256)
SECRET_KEY ≥64 hex chars
# Customers use separate key (HS256)
CUSTOMER_SECRET_KEY ≥64 hex chars (must differ from SECRET_KEY)
# Verification: algorithm="HS256", no `options={"verify_signature": False}`
```

**Customer OTP Auth:**
- Method: SMS (Twilio) or Email (SMTP/SendGrid)
- Generation: `secrets.token_digits(6)` (cryptographically secure)
- Storage: SHA-256 hash in Redis
- TTL: 5 minutes (`CUSTOMER_OTP_TTL_SECONDS`)
- Rate limit: 2-minute cooldown, 5 per day per customer
- Attempts: Max 3 wrong attempts before lockout
- JWT: 7 days for customer auth tokens

**Identity Linking:**
- Table: `identity_link` (tenant_id, external_provider, external_user_id, internal_user_id)
- JIT provisioning: Enabled via `EXTERNAL_AUTH_JIT_ENABLED` (off by default)
- When disabled: External tokens without a link row are rejected with 401 IDENTITY_LINK_REQUIRED

## Monitoring & Observability

**Error Tracking:**
- Sentry (fastapi)
  - Config: `SENTRY_DSN` (optional; if empty, no error tracking)
  - When enabled: All unhandled exceptions automatically reported
  - Integration: `sentry-sdk[fastapi]` middleware

**Logging:**
- Structlog (JSON)
  - Output: stdout (structured, parseable by log aggregators)
  - Format: ISO 8601 timestamps, log_level, exception info
  - Context: Tenant ID propagated via contextvars
  - Never log: Phone numbers, emails, SSN, raw OTP values, JWT tokens

**Instrumentation:**
- OpenTelemetry API 1.24.0 (hooks for future tracing setup)

## CI/CD & Deployment

**Hosting:**
- Container-based (Docker)
- Web server: Gunicorn 22.0.0 (production)
- Task worker: Celery with Redis broker
- Beat scheduler: Celery beat for hourly/periodic tasks

**Local Development:**
- `docker-compose.yml` stack: FastAPI + PostgreSQL/PostGIS + Redis + Celery
- Hot reload: `--reload` flag on uvicorn during dev
- Database: Migrations auto-run at container startup (`alembic upgrade head`)

**Environment Configuration:**

Required env vars for startup (all in `.env.example`):
```
# Core
SECRET_KEY                      # ≥64 hex chars (owners)
CUSTOMER_SECRET_KEY             # ≥64 hex chars (customers, must differ)
ENVIRONMENT                     # local|staging|production
DATABASE_URL                    # postgres://... (async)
REDIS_URL                       # redis://...

# Auth
ENCRYPTION_KEY_V1               # 32 bytes base64 (AES-256-GCM)
JWT_ACCESS_TTL_MINUTES          # Owner JWT TTL (default 15)
CUSTOMER_JWT_TTL_DAYS           # Customer JWT TTL (default 7)

# AI Providers
OPENAI_API_KEY                  # GPT-4o API key
GEMINI_API_KEY                  # Gemini fallback API key

# SMS/WhatsApp
TWILIO_ACCOUNT_SID              # Twilio account
TWILIO_AUTH_TOKEN               # Twilio auth
TWILIO_FROM_NUMBER              # SMS sender number
TWILIO_WHATSAPP_FROM            # WhatsApp sender

# Email
SMTP_USERNAME                   # Gmail app password (spaces stripped at startup)
SMTP_PASSWORD
EMAIL_FROM_ADDRESS

# Billing
RAZORPAY_KEY_ID                 # Razorpay order creation
RAZORPAY_KEY_SECRET
RAZORPAY_WEBHOOK_SECRET         # X-Razorpay-Signature HMAC key

# Authentication Providers
FIREBASE_PROJECT_ID             # Firebase project
FIREBASE_SERVICE_ACCOUNT_JSON   # Inline JSON or path
FIREBASE_WEB_API_KEY            # Client-side (safe)
FIREBASE_WEB_AUTH_DOMAIN        # Client-side (safe)

SUPABASE_PROJECT_REF            # Bare ref (no scheme/dots)
SUPABASE_URL
SUPABASE_ANON_KEY

# Google OAuth
GOOGLE_CLIENT_ID                # For GMB OAuth flow
GOOGLE_CLIENT_SECRET
GMB_OAUTH_REDIRECT_URI

# Object Storage
R2_ACCOUNT_ID                   # Cloudflare R2
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET_NAME

# Observability
SENTRY_DSN                      # (optional)
```

**Secrets location:**
- `.env` file (gitignored, never committed)
- Environment variables at runtime (recommended for containers)
- Startup validation: Missing required secrets = startup failure

## Webhooks & Callbacks

**Incoming Webhooks:**
- Razorpay payment events
  - Endpoint: `POST /api/v1/webhooks/razorpay`
  - Signature verification: HMAC-SHA256 via `app/core/razorpay_signature.py`
  - Events: `payment.authorized`, `payment.failed`, `subscription.activated`, `subscription.updated`
  - Handler: `app/services/billing_service.py`

**Outgoing Webhooks:**
- GMB review responses
  - Sent via Google My Business API (no WebSocket; synchronous HTTP responses)
  - Service: `app/services/response_service.py`
  - Content: AI-drafted review response text

**Callbacks (OAuth):**
- Google OAuth redirect
  - URI: `{GMB_OAUTH_REDIRECT_URI}` (must match registered URI on Google Cloud Console)
  - Handler: `app/api/v1/routers/auth.py` — `/callback/google` endpoint
  - Result: Authorization code → exchange for refresh token → store in DB

---

*Integration audit: 2026-08-19*
