# Technology Stack

**Analysis Date:** 2026-08-19

## Languages

**Primary:**
- Python 3.12 - All backend code, services, API, migrations, Celery workers

## Runtime

**Environment:**
- Python 3.12+ (pinned in `pyproject.toml`)
- Uvicorn 0.29.0 - ASGI development server with hot reload
- Gunicorn 22.0.0 - Production WSGI server

**Package Manager:**
- pip with requirements.txt
- Lockfiles: `requirements/base.txt`, `requirements/prod.txt`, `requirements/dev.txt`

## Frameworks

**Core:**
- FastAPI 0.111.0 - REST API framework
- Pydantic 2.7.0 - Data validation and settings management via BaseModel (v2 syntax required)

**Database & ORM:**
- SQLAlchemy 2.0.30 - Async ORM with `AsyncSession`, `async_sessionmaker`
- Alembic 1.13.1 - Database migrations (one file per schema change)
- Asyncpg 0.29.0 - Async PostgreSQL driver
- GeoAlchemy2 0.14.0 - PostGIS geometry column support

**Task Queue & Caching:**
- Celery 5.3.6 - Distributed task queue with JSON serialization
- Redis 5.0.4 - Broker, result backend, cache, OTP storage

**Authentication & Security:**
- PyJWT 2.8.0 - JWT encode/decode (HS256 algorithm required)
- Passlib 1.7.4 - Password hashing interface
- Bcrypt < 4.0.0 - Bcrypt password hashing (12 rounds, never lower)
- PyOTP 2.9.0 - TOTP for MFA
- Cryptography 42.0.7 - AES-256-GCM encryption for PII
- Authlib 1.3.0 - OAuth framework
- SlowAPI 0.1.9 - Rate limiting via token bucket

**Rate Limiting:**
- SlowAPI 0.1.9 - Per-endpoint rate limiting with Retry-After header

**Logging:**
- Structlog 24.2.0 - Structured JSON logging with contextvars

**Testing:**
- pytest 8.2.0 - Test runner
- pytest-asyncio 0.23.6 - Async test support
- pytest-cov 5.0.0 - Code coverage reporting
- pytest-mock 3.14.0 - Mocking fixture (mocker)
- pytest-playwright 0.5.1 - Browser automation for E2E tests

**Security Scanning:**
- Bandit 1.7.8 - Static security analysis
- Safety 3.2.0 - Dependency vulnerability scanning

**Code Quality:**
- Ruff 0.4.5 - Fast Python linter (isort, E/F/W rules)

**Observability:**
- Sentry 2.5.0 (fastapi) - Error tracking (optional, via `SENTRY_DSN`)
- OpenTelemetry API 1.24.0 - Observability instrumentation hooks

**Templating:**
- Jinja2 3.1.4 - HTML template rendering (Stitch.ai exports + wiring)

**HTTP Client:**
- httpx 0.27.0 - Async HTTP client for external APIs

**Utilities:**
- qrcode 7.4.2 + Pillow - QR code generation for loyalty receipts
- Shapely 2.0.4 - Geospatial geometry calculations
- python-multipart 0.0.9 - Multipart form parsing for FastAPI

## Key Dependencies

**Critical (Product Features):**
- openai 1.30.0 - GPT-4o for AI review generation (primary provider)
- google-generativeai 0.8.5 - Gemini 1.5 Pro fallback when OpenAI fails or disabled
- geoalchemy2 0.14.0 - PostGIS integration for geofence scanning
- celery + redis - Background task scheduling (REVIEW-02 hourly draft generation, Firestore projection drain)

**Infrastructure & Auth:**
- firebase-admin 6.5.0 - Firebase ID token verification + Firestore projection writes
- twilio 9.1.0 - SMS/WhatsApp OTP delivery (SMS) and opt-in upgrades (WhatsApp Pro+)
- aiosmtplib 3.0.1 - Async SMTP for email delivery (never blocks event loop)
- sendgrid 6.11.0 - SendGrid email API (EMAIL_PROVIDER=sendgrid fallback)
- razorpay 1.4.2 - Indian payment gateway (subscription orders, refunds)

## Configuration

**Environment:**
- Pydantic BaseSettings in `app/core/config.py`
- Loads from `.env` file (gitignored, `.env.example` provided)
- Startup validation: missing required secrets = startup failure with clear error
- All secrets must be >= 64 hex chars (SECRET_KEY, CUSTOMER_SECRET_KEY)

**Build & Deployment:**
- `pyproject.toml` - Project metadata, pytest config, ruff linting rules
- `docker-compose.yml` - Local dev stack (FastAPI, PostgreSQL/PostGIS, Redis, Celery)
- `Dockerfile` - Multi-stage build for production image (not included in analysis)

## Platform Requirements

**Development:**
- Python 3.12+
- PostgreSQL 16 with PostGIS 3.4 extension (via Supabase or local `docker-compose`)
- Redis 7 (for cache, OTP storage, Celery broker)
- Docker + docker-compose (optional, for local full stack)

**Production:**
- PostgreSQL 16+ (Supabase recommended for PostGIS + managed backups)
- Redis 7+ (Upstash recommended for serverless)
- Container runtime (Docker/Kubernetes)
- SMTP credentials (Gmail app password or SendGrid API key)
- All env vars from `.env.example` properly configured before startup

**Async Patterns:**
- Event loop: uvicorn runs single asyncio loop
- No `asyncio.run()` inside FastAPI (already in async context)
- No `time.sleep()` (use `await asyncio.sleep()`)
- Prepared statement caching disabled when using Supabase transaction pooler

---

*Stack analysis: 2026-08-19*
