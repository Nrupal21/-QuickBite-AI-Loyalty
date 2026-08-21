# Codebase Structure

**Analysis Date:** 2026-08-19

## Directory Layout

```
[project-root]/
├── app/                           # Source code
│   ├── main.py                    # FastAPI app entry point
│   ├── core/                      # Cross-cutting concerns
│   │   ├── config.py              # Pydantic BaseSettings (env vars)
│   │   ├── security.py            # JWT, bcrypt, TOTP for staff
│   │   ├── customer_security.py   # Customer JWT (separate key)
│   │   ├── principal.py           # Authenticated identity abstraction
│   │   ├── rbac.py                # Role hierarchy + RoleLevel
│   │   ├── encryption.py          # AES-256-GCM + SHA-256 for PII
│   │   ├── rate_limiter.py        # SlowAPI instance + endpoints
│   │   ├── cache_service.py       # Redis async client
│   │   ├── firebase_auth.py       # Firebase ID token verifier
│   │   ├── supabase_auth.py       # Supabase JWKS + JWT verifier
│   │   ├── jwks_cache.py          # Supabase JWKS caching (10min TTL)
│   │   ├── email_transport.py     # SMTP/SendGrid async client
│   │   ├── firestore_client.py    # Firestore async client (billing projection)
│   │   ├── anti_fraud.py          # Fraud detection patterns
│   │   ├── prompt_guard.py        # AI prompt injection defense
│   │   ├── broadcast.py           # WebSocket broadcast (unused yet)
│   │   ├── feature_flags.py       # Feature flag toggles (unused yet)
│   │   ├── razorpay_signature.py  # Razorpay HMAC verification
│   │   └── __init__.py            # Core module exports
│   ├── db/                        # Database layer
│   │   ├── base.py                # AsyncEngine, async_session_factory, get_db()
│   │   ├── rls.py                 # Row-level security: set_tenant_context(), admin_bypass_context()
│   │   ├── bootstrap.py           # Utility for reference data seeding
│   │   ├── models/                # SQLAlchemy ORM models
│   │   │   ├── __init__.py        # Model imports
│   │   │   ├── tenant.py          # Tenant (restaurant root)
│   │   │   ├── user.py            # User (staff), Role, Session
│   │   │   ├── customer.py        # Customer (loyalty member), ReviewDraft
│   │   │   ├── branch.py          # Branch (location with PostGIS geometry)
│   │   │   ├── subscription.py    # Subscription (plan + renewal)
│   │   │   ├── payment.py         # Payment, Order, Ledger, Outbox
│   │   │   ├── loyalty.py         # StampLog, RewardProgram, RewardRedemption
│   │   │   ├── reputation.py      # Review, Response
│   │   │   ├── identity_link.py   # IdentityLink (external provider → local user/customer)
│   │   │   ├── audit.py           # AuditLog
│   │   │   └── static_data.py     # SubscriptionPlan (reference data)
│   │   └── migrations/            # Alembic versions (never edit!)
│   │       ├── env.py             # Alembic config
│   │       ├── script.py.mako     # Template
│   │       └── versions/          # One file per migration (auto-named)
│   ├── api/
│   │   └── v1/
│   │       ├── routers/           # HTTP handlers (thin layers)
│   │       │   ├── __init__.py    # api_router combines all
│   │       │   ├── auth.py        # /auth routes (staff login, register, MFA, refresh)
│   │       │   ├── customer_auth.py # /customer routes (OTP, loyalty login)
│   │       │   ├── loyalty.py     # /loyalty routes (scan, rewards, analytics)
│   │       │   ├── reputation.py  # /reviews routes + /gmb webhook routes
│   │       │   ├── billing.py     # /billing + /webhooks/razorpay routes
│   │       │   ├── team.py        # /team routes (members, invite, remove)
│   │       │   ├── customers.py   # /customers routes (lookup, manage)
│   │       │   ├── dashboard.py   # /dashboard routes (KPIs, analytics)
│   │       │   ├── admin.py       # /admin routes (super admin only)
│   │       │   └── pages.py       # Server-rendered templates (Jinja2)
│   │       └── dependencies/      # FastAPI dependency injection
│   │           ├── __init__.py
│   │           ├── auth.py        # resolve_principal(), get_current_user(), require_role()
│   │           ├── customer_auth.py # get_current_customer_optional()
│   │           └── subscription.py # tenant_has_feature(), check_subscription_tier()
│   ├── services/                  # Business logic (no HTTP, no DB calls directly)
│   │   ├── __init__.py
│   │   ├── auth_service.py        # Registration, login, MFA, token refresh
│   │   ├── customer_otp_service.py # OTP generation/verification
│   │   ├── customer_service.py    # Customer lookup, signup, profile updates
│   │   ├── loyalty_service.py     # QR scan, stamp, rewards, geofence
│   │   ├── gmb_service.py         # Google My Business sync, responses
│   │   ├── review_service.py      # Review queries, filtering
│   │   ├── review_sync_service.py # Sync orchestration for REVIEW-03
│   │   ├── response_service.py    # AI response composition, safety checks
│   │   ├── ai_engine.py           # OpenAI/Gemini wrapper with fallback
│   │   ├── billing_service.py     # Razorpay checkout, subscription mgmt
│   │   ├── dashboard_service.py   # KPI aggregation
│   │   ├── team_service.py        # Team member CRUD, invitations
│   │   ├── identity_service.py    # Email/phone/username identify endpoint
│   │   ├── identity_link_service.py # Map external tokens to local rows
│   │   ├── identity_oauth_service.py # OAuth state/token exchange (unused yet)
│   │   ├── customer_oauth_service.py # Customer Firebase OAuth flow
│   │   ├── admin_service.py       # Super admin operations
│   │   ├── notification_service.py # Email/SMS sending (uses email_transport)
│   │   ├── messaging_service.py   # WhatsApp/SMS messaging
│   │   ├── projection_service.py  # Write Firestore mirror (billing projection)
│   │   ├── usage_service.py       # Track API usage against plan limits
│   │   ├── email_renderer.py      # Render Jinja2 email templates
│   │   ├── geofence_service.py    # PostGIS distance calculation
│   │   ├── qr_service.py          # Placeholder for QR generation
│   │   └── onboarding_service.py  # Placeholder for onboarding flow
│   ├── schemas/                   # Pydantic request/response models
│   │   ├── __init__.py
│   │   ├── auth.py                # UserRegister, TokenResponse, MFAEnroll*, etc.
│   │   ├── customer_auth.py       # OTPRequest, OTPVerify, CustomerLogin
│   │   ├── loyalty.py             # ScanRequest, ScanResponse, RewardProgram*
│   │   ├── reputation.py          # ReviewResponse, ResponseRequest, etc.
│   │   ├── billing.py             # CheckoutRequest, SubscriptionResponse
│   │   ├── customers.py           # CustomerResponse, CustomerUpdate
│   │   ├── dashboard.py           # LoyaltyAnalyticsResponse, KPIs
│   │   ├── team.py                # TeamMember, InviteRequest
│   │   ├── admin.py               # SuperAdminMetrics
│   │   ├── identity.py            # LinkIdentityRequest, LinkIdentityResponse
│   │   └── common.py              # Shared enums (role names, etc.)
│   ├── templates/                 # Jinja2 server-rendered HTML
│   │   ├── customer/
│   │   │   ├── login.html
│   │   │   ├── register.html
│   │   │   └── verify_email.html
│   │   ├── dashboard/
│   │   │   └── index.html
│   │   ├── billing/
│   │   │   ├── checkout.html
│   │   │   └── portal.html
│   │   ├── landing/
│   │   │   └── index.html        # Public home + Three.js hero
│   │   ├── emails/
│   │   │   ├── verify_email.html
│   │   │   ├── invite_team.html
│   │   │   └── reward_unlock.html
│   │   └── base.html             # Base template
│   ├── workers/                   # Async background tasks
│   │   ├── __init__.py
│   │   ├── celery_app.py         # Celery + Redis config, Beat schedule
│   │   └── tasks.py              # sync_all_gmb_profiles, batch_generate_ai_responses, drain_projection_outbox
│   └── middleware/                # ASGI middleware
│       ├── __init__.py
│       └── subdomain.py          # TenantMiddleware (extract subdomain → tenant_id)
├── static/                        # Static assets
│   ├── css/
│   │   ├── auth.css              # Auth pages styling
│   │   └── dashboard.css         # Dashboard styling
│   └── js/
│       ├── auth-login.js         # Login form + identify-first flow
│       ├── auth-register.js      # Registration + email verification
│       └── loyalty-scan.js       # QR scan + geofence
├── tests/                         # Unit + integration tests
│   ├── unit/
│   │   ├── test_auth_login.py
│   │   ├── test_auth_register.py
│   │   ├── test_customer_otp.py
│   │   ├── test_loyalty_scan.py
│   │   ├── test_response_service.py
│   │   ├── test_review_composer.py
│   │   └── conftest.py           # Fixtures (db session, mocks)
│   ├── integration/
│   │   └── (TODO: integration test suite)
│   └── security/
│       └── (TODO: security-focused attack tests)
├── scripts/                       # Utility scripts
│   ├── seed_plans.py             # Insert subscription plans
│   ├── seed_roles.py             # Insert RBAC roles
│   ├── migrate.py                # Alembic migration runner (for deployment)
│   └── populate_test_data.py     # Generate test tenant/users
├── .claude/                       # Claude Code tools + settings
│   └── skills/                    # (skills directory not used yet)
├── .planning/                     # Generated codebase maps
│   └── codebase/
│       ├── ARCHITECTURE.md        # (you are here)
│       ├── STRUCTURE.md           # (you are here)
│       ├── CONVENTIONS.md         # (TODO)
│       ├── TESTING.md             # (TODO)
│       ├── CONCERNS.md            # (TODO)
│       ├── STACK.md               # (TODO)
│       └── INTEGRATIONS.md        # (TODO)
├── alembic.ini                    # Alembic config
├── pyproject.toml                 # Poetry project definition
├── pytest.ini                     # Pytest config
├── .env.example                   # Template env vars (committed)
├── .env                           # Secrets (gitignored)
├── .gitignore
├── Makefile                       # Dev commands (docker-compose up, tests, etc.)
├── Dockerfile                     # Production image
├── docker-compose.yml             # Local dev stack (postgres, redis)
├── AGENTS.md                      # Shared team rules
├── CLAUDE.md                      # Claude Code session rules
└── GEMINI.md                      # Gemini session rules
```

## Directory Purposes

**app/core/:**
- Purpose: Shared infrastructure, global configuration, identity models, external SDK wrappers
- Contains: Type systems (Principal, RoleLevel), cryptography (JWT, bcrypt, AES), auth verifiers (Firebase, Supabase), external clients (Redis, SMTP, Firestore), rate limiting
- Key files: `config.py` (always imported first, validates env vars), `principal.py` (identity model), `security.py` (JWT/bcrypt), `rbac.py` (role hierarchy)

**app/db/:**
- Purpose: Data persistence, async session management, row-level security, schema definitions
- Contains: SQLAlchemy engine config, async session factory, RLS context binding, 13 ORM models, Alembic migrations
- Key files: `base.py` (engine + get_db dependency), `rls.py` (set_tenant_context), models (never modify directly, changes via migrations)

**app/api/v1/routers/:**
- Purpose: HTTP request handlers, input validation, response serialization
- Contains: One file per domain (auth, loyalty, billing, etc.), each with 2-8 endpoints
- Pattern: Every endpoint is a thin wrapper — extract request, call service, return response. No business logic.
- Key files: `auth.py` (owner login/MFA), `loyalty.py` (scan/rewards), `reputation.py` (GMB sync)

**app/api/v1/dependencies/:**
- Purpose: FastAPI dependency injection, authentication, authorization
- Contains: Principal resolution, role checking, feature flagging
- Key files: `auth.py` (resolve_principal for all token families, require_role), `customer_auth.py` (customer OTP tokens)

**app/services/:**
- Purpose: Business logic, database access, external API orchestration
- Contains: Classes instantiated per-request with (session, tenant_id), methods that accept primitives/ORM, return Pydantic schemas
- Pattern: No global state. Avoid circular imports (use function-scoped imports if needed).
- Key files: `auth_service.py` (registration, login), `loyalty_service.py` (scan validation), `ai_engine.py` (LLM wrapper)

**app/schemas/:**
- Purpose: Request/response validation contracts, type safety at API boundary
- Contains: Pydantic BaseModel classes
- Pattern: Define before routes. Use `Field(description="...")` for OpenAPI docs. Use enums for constrained values.
- Key files: One per domain (auth.py, loyalty.py, billing.py, etc.)

**app/templates/:**
- Purpose: Server-rendered HTML views (Jinja2)
- Contains: Subdirectories per section (customer/, dashboard/, landing/, emails/)
- Pattern: Base template with blocks; child templates extend and override. Context passed from routers via JSONResponse or render_template.
- Key files: `customer/login.html` (identify-first flow), `landing/index.html` (Three.js hero)

**app/workers/:**
- Purpose: Async background tasks scheduled by Celery Beat
- Contains: Celery app config (Redis broker), three scheduled tasks
- Pattern: Tasks must be idempotent (check Redis key before side effects). JSON-serializable args only.
- Key files: `celery_app.py` (Beat schedule), `tasks.py` (sync_all_gmb_profiles, batch_generate_ai_responses, drain_projection_outbox)

**tests/:**
- Purpose: Automated verification
- Contains: Unit tests (isolated services + models), integration tests (full request paths), security tests (attack scenarios)
- Pattern: Fixtures for db session + mocked external APIs. One test per acceptance criterion.

**scripts/:**
- Purpose: One-off utilities, deployment helpers
- Contains: Data seeding (roles, plans), migration runners, test data generation
- Pattern: Run locally or in CI/CD, not in production code.

## Key File Locations

**Entry Points:**
- `app/main.py`: FastAPI application, startup event, structlog config, router registration
- `app/api/v1/routers/auth.py:59`: POST /api/v1/auth/identify (identify-first login screen)
- `app/api/v1/routers/auth.py:84`: POST /api/v1/auth/login (owner/staff login)
- `app/api/v1/routers/customer_auth.py`: POST /customer/request-otp (loyalty member signup)
- `app/api/v1/routers/loyalty.py:40`: POST /api/v1/loyalty/scan (customer QR scan)
- `app/api/v1/routers/pages.py`: GET / (landing), GET /verify-email (email verification)
- `app/workers/tasks.py`: Celery task definitions (runs on schedule)

**Configuration:**
- `app/core/config.py`: Pydantic Settings, validates all env vars at import time
- `.env.example`: Template (committed); `.env` secrets (gitignored)
- `alembic.ini`: Alembic configuration (SQLAlchemy section)
- `pyproject.toml`: Poetry dependencies + scripts
- `.claude/`: Claude Code configuration (CLAUDE.md, settings.json)

**Core Logic:**
- `app/services/auth_service.py`: Registration, email verification, login, MFA, refresh token rotation
- `app/services/loyalty_service.py`: Scan validation, geofence check, stamp logging, reward program CRUD
- `app/services/response_service.py`: AI response composition (OpenAI/Gemini), safety checks
- `app/services/billing_service.py`: Razorpay integration, subscription tracking, plan enforcement
- `app/core/principal.py`: Authenticated identity model (USER | CUSTOMER, LOCAL | SUPABASE | FIREBASE)
- `app/core/rbac.py`: Role hierarchy (SUPER_ADMIN=1, OWNER=2, MANAGER=3, STAFF=4)

**Testing:**
- `tests/conftest.py`: Global fixtures (db session, mocked clients, test tenant)
- `tests/unit/test_auth_login.py`: Login flow + MFA
- `tests/unit/test_loyalty_scan.py`: Geofence, rate limiting, fraud detection
- `tests/unit/test_response_service.py`: AI response validation

**Database:**
- `app/db/base.py`: Async engine, session factory
- `app/db/rls.py`: set_tenant_context(), admin_bypass_context()
- `app/db/models/user.py`: User, Role, Session models
- `app/db/models/customer.py`: Customer, ReviewDraft models
- `app/db/models/tenant.py`: Tenant (root) model
- `app/db/models/loyalty.py`: StampLog, RewardProgram, RewardRedemption
- `app/db/migrations/`: Alembic versions (one per schema change)

## Naming Conventions

**Files:**
- Services: `{domain}_service.py` (e.g., `auth_service.py`, `loyalty_service.py`)
- Routers: `{domain}.py` (e.g., `auth.py`, `loyalty.py`)
- Models: `{entity}.py` (e.g., `user.py`, `customer.py`)
- Schemas: `{domain}.py` (e.g., `auth.py`, `loyalty.py`)
- Tests: `test_{module}.py` (e.g., `test_auth_login.py`, `test_loyalty_scan.py`)
- **Avoid:** generic names like `utils.py`, `helpers.py`. Be specific: `encryption_utils.py`, `geofence_utils.py`.

**Directories:**
- `routers/` — HTTP handlers (not `routes/`, not `endpoints/`)
- `services/` — business logic layer
- `models/` — SQLAlchemy ORM (not `database/`)
- `schemas/` — Pydantic models (not `dto/`, not `models/`)
- `templates/` — Jinja2 HTML (not `views/`)
- `workers/` — Celery tasks (not `tasks/`, not `background/`)
- `core/` — cross-cutting (not `common/`)

**Functions:**
- HTTP handlers: `async def endpoint_name(...)` in routers
- Service methods: `async def operation_name(self, ...)` in service classes
- Schemas: PascalCase for classes (e.g., `ScanRequest`), snake_case for fields (e.g., `gps_lat`)
- Dependencies: `async def get_something(...)` or `async def require_something(...)`

**Variables:**
- Models/ORM: snake_case (e.g., `user_id`, `tenant_id`)
- Database columns: snake_case (e.g., `created_at`, `email_hash`)
- Constants: UPPER_CASE (e.g., `BCRYPT_ROUNDS`, `OTP_TTL_SECONDS`)
- Private (module-level): `_leading_underscore` (e.g., `_UNAUTHORIZED` exception)

## Where to Add New Code

**New Feature (e.g., LOYALTY-03 geofence-based scan):**

1. **Define schema** in `app/schemas/loyalty.py`:
   ```python
   class ScanRequest(BaseModel):
       qr_token: str
       gps_lat: float = Field(ge=-90, le=90)
       gps_lng: float = Field(ge=-180, le=180)
   ```

2. **Create/update service** in `app/services/loyalty_service.py`:
   ```python
   class LoyaltyService:
       async def process_scan(self, qr_token: str, gps_lat: float, gps_lng: float, ...) -> ScanResponse:
           # Validate, access DB, return response schema
   ```

3. **Add route** in `app/api/v1/routers/loyalty.py`:
   ```python
   @router.post("/scan", response_model=ScanResponse)
   @limiter.limit("6/hour")
   async def scan_loyalty_qr(
       request: Request,
       body: ScanRequest,
       session: AsyncSession = Depends(get_db),
       current_customer: Customer | None = Depends(get_current_customer_optional),
   ) -> ScanResponse:
       service = LoyaltyService(session=session)
       return await service.process_scan(...)
   ```

4. **Add test** in `tests/unit/test_loyalty_scan.py`:
   ```python
   @pytest.mark.asyncio
   async def test_scan_within_geofence(db_session, test_branch):
       service = LoyaltyService(session=db_session, tenant_id=test_branch.tenant_id)
       response = await service.process_scan(...)
       assert response.stamp_count == 1
   ```

5. **Add migration** if schema changes needed:
   ```bash
   alembic revision --autogenerate -m "add_scan_rate_limit_flag"
   alembic upgrade head
   ```

**New Model/Table:**

1. Create `app/db/models/{entity}.py`:
   ```python
   from app.db.base import Base
   
   class MyEntity(Base):
       __tablename__ = "my_entities"
       __table_args__ = {"schema": "restaurant"}  # or "customer" or "static"
       
       tenant_id: Mapped[uuid.UUID] = mapped_column(...)
       # ... fields
   ```

2. Create Alembic migration:
   ```bash
   alembic revision --autogenerate -m "create_my_entities_table"
   ```

3. Update `app/db/models/__init__.py` to export the new model

4. Create service method to query it:
   ```python
   async def get_my_entity(self, entity_id: uuid.UUID) -> MyEntity | None:
       result = await self.session.execute(select(MyEntity).where(MyEntity.id == entity_id))
       return result.scalar_one_or_none()
   ```

**New Dependency (Auth/Subscription Check):**

Add to `app/api/v1/dependencies/auth.py` or `subscription.py`:
```python
async def require_feature(feature_name: str):
    async def dependency(principal: Principal = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> bool:
        return await tenant_has_feature(session, principal.tenant_id, feature_name)
    return dependency
```

Use in route:
```python
@router.post("/my-endpoint")
async def my_endpoint(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    has_feature: bool = Depends(require_feature("advanced_analytics")),
):
```

**New External API Integration:**

1. Add config to `app/core/config.py`:
   ```python
   MY_API_KEY: str = ""
   MY_API_BASE_URL: str = ""
   ```

2. Create client wrapper in `app/core/my_api_client.py`:
   ```python
   async def call_my_api(payload: dict) -> dict:
       async with httpx.AsyncClient() as client:
           response = await client.post(settings.MY_API_BASE_URL + "/endpoint", ...)
   ```

3. Create service to use it in `app/services/my_integration_service.py`:
   ```python
   class MyIntegrationService:
       async def do_something(self) -> Result:
           result = await call_my_api(...)
   ```

4. Mock in tests:
   ```python
   @pytest.fixture
   def mock_my_api(mocker):
       return mocker.patch("app.core.my_api_client.call_my_api")
   ```

**New Background Task:**

1. Add to `app/workers/tasks.py`:
   ```python
   @celery_app.task(bind=True, max_retries=3)
   def my_background_task(self, tenant_id: str, resource_id: str):
       idempotency_key = f"task:{resource_id}:done"
       if cache_service.exists(idempotency_key):
           return
       # ... do work
       cache_service.set(idempotency_key, "1", ttl=86400)
   ```

2. Schedule in `app/workers/celery_app.py`:
   ```python
   beat_schedule={
       "my-task": {
           "task": "app.workers.tasks.my_background_task",
           "schedule": 3600.0,  # every hour
       },
   }
   ```

3. Or trigger manually from route:
   ```python
   my_background_task.delay(tenant_id=str(principal.tenant_id), resource_id=str(resource_id))
   ```

## Special Directories

**app/db/migrations/:**
- Purpose: Version control for schema changes
- Generated: Yes, by Alembic
- Committed: Yes, to git
- **Never edit existing files.** Only add new revision files. Old migrations are historical record.
- Run locally: `alembic upgrade head`
- Run in CI/CD (production): `alembic upgrade head`

**static/:**
- Purpose: CSS, JS, images (served by `app.mount("/static", StaticFiles(...))`)
- Generated: No
- Committed: Yes, to git
- CSS: Tailwind utility classes (no hand-written CSS)
- JS: Minimal vanilla JS or library integrations (Anime.js, GSAP)

**templates/:**
- Purpose: Server-rendered HTML via Jinja2
- Generated: No (hand-written or Stitch.ai exports)
- Committed: Yes, to git
- Pattern: Use `{% extends "base.html" %}`, override `{% block content %}`
- Context passed from routes: `return templates.TemplateResponse("customer/login.html", {"request": request, ...})`

**.env & .env.example:**
- Purpose: Environment variables
- `.env`: Secrets, never committed (in .gitignore)
- `.env.example`: Template with empty values, committed for reference
- Required vars: all marked as `str` without defaults in `Settings` class

**scripts/:**
- Purpose: One-off utilities (seeding, migration, test data)
- Pattern: Async functions, import FastAPI app + settings, use session factory
- Example: `python scripts/seed_roles.py` (run after first migration)

---

*Structure analysis: 2026-08-19*
