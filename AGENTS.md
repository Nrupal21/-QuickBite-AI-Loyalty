# QuickBite AI + Loyalty — Shared Agent Rules
# Read by: Antigravity (GEMINI.md), Claude Code (CLAUDE.md), Cursor (.cursorrules), Windsurf

## Project Identity
You are building **QuickBite AI + Loyalty** — a multi-tenant SaaS for Indian restaurant owners.
One QR code on a receipt → customer scans → AI review drafted in < 30 seconds + loyalty stamp collected.
No app download. OTP-only login (phone SMS or email). Never username/password for customers.

## The Full Documentation Suite (read before coding any feature)
- Doc 1 — PRD: what the product does, personas, success metrics
- Doc 2 — Technical Architecture: tech stack, folder structure, all 15 DB tables, env vars
- Doc 3 — Security & Access: auth flows, RBAC, OTP rules, error handling
-
- Doc 5 — Feature Tickets: 70 tickets with acceptance criteria — this is your task list
- Doc 6 — System Architecture + Team Plan: sprint plan, RACI, collaboration rules
- Dev Guide — AI agents, IDEs, branch workflow, hour estimates, progress reports

When implementing any ticket, always read the relevant acceptance criteria from Doc 5 first.

---

## 1. Technology Stack — Never Deviate

### Backend
- **Python 3.12** — no older versions
- **FastAPI 0.111** with Pydantic v2 models (NOT v1 syntax)
- **SQLAlchemy 2.x async** — always use `async_session`, `await session.execute()`
- **Alembic** for all DB migrations — never raw `CREATE TABLE`
- **Celery 5.3** with Redis as broker — for all background tasks
- **Structlog** for all logging — never `print()` or `logging.basicConfig()`

### Database
- **PostgreSQL 16 + PostGIS 3.4** via Supabase
- **Redis 7** (Upstash) for cache, OTP storage, Celery broker
- **GeoAlchemy2** for PostGIS geometry columns
- **Asyncpg** as the async PostgreSQL driver

### Authentication
- **PyJWT** for JWT — algorithm MUST be `HS256`, NEVER `alg:none`
- **Passlib + bcrypt** with `rounds=12` — never lower
- **PyOTP** for TOTP MFA
- **Python `secrets` module** for all OTP and token generation
- **SlowAPI** for all rate limiting

### Frontend
- **Stitch.ai** for ALL screen generation — never hand-write full HTML pages
- **Jinja2** for template wiring — `{{ variable }}` and `{% for %}` blocks
- **Tailwind CSS 3.4** — only utility classes from Doc 4 palette
- **Three.js r128** — landing page hero ONLY
- **GSAP 3.12 + ScrollTrigger** — landing page ONLY
- **Anime.js 3.2.1** — all complex JS animations (loyalty, reward, stamp, dashboard)
- **Animate.css 4.1.1** — all CSS utility animations (entrances, shakes, toasts)

### External Services
- **Twilio** — SMS OTP delivery + WhatsApp (Pro+ only). `SMS_PROVIDER=2factor` (`app/core/config.py`) swaps OTP-only SMS to 2Factor.in (India, no-DLT-template quick-OTP API) — WhatsApp and non-OTP SMS stay on Twilio regardless; see `app/services/messaging_service.py`.
- **SendGrid** — Email OTP + campaigns
- **Google My Business API v4.9** — review sync + response posting
- **Stripe 2024-04-10** — subscription billing
- **OpenAI GPT-4o** — AI review composer (primary)
- **Google Gemini 1.5 Pro** — AI fallback (Redis feature flag)
- **Cloudflare R2** — QR code PNGs, exports, logos
- **qrcode** (Python) — QR code generation

---

## 2. Folder Structure — Exact Match Required

```
app/
├── main.py                 # FastAPI app + middleware
├── core/
│   ├── config.py           # Pydantic BaseSettings (all env vars from .env)
│   ├── security.py         # JWT encode/decode, bcrypt hash/verify
│   ├── dependencies.py     # get_current_user(), require_role(), check_subscription_tier()
│   └── middleware.py       # SubdomainTenantMiddleware (ASGI)
├── db/
│   ├── base.py             # SQLAlchemy async engine, get_db() session factory
│   ├── models/             # One file per domain (user.py, customer.py, review.py ...)
│   └── migrations/         # Alembic versions/
├── api/
│   └── v1/
│       ├── auth/           # owner auth, customer OTP auth
│       ├── reviews/        # GMB sync, AI generation, approval
│       ├── loyalty/        # branches, QR, geofence scan, rewards
│       ├── dashboard/      # stats, WebSocket, analytics
│       ├── billing/        # Stripe checkout, webhooks, portal
│       └── admin/          # Super Admin endpoints
├── services/               # Business logic (no DB calls, no HTTP handlers)
│   ├── customer_otp_service.py
│   ├── ai_review_service.py
│   ├── gmb_service.py
│   ├── geofence_service.py
│   ├── loyalty_service.py
│   └── qr_service.py
├── tasks/                  # Celery tasks
│   ├── gmb_tasks.py
│   ├── loyalty_tasks.py
│   └── campaign_tasks.py
├── schemas/                # Pydantic request/response models
└── templates/              # Jinja2 HTML from Stitch.ai exports
    ├── customer/
    ├── dashboard/
    ├── billing/
    ├── admin/
    └── landing/
```

Never create files outside this structure. If unsure, ask before adding a new module.

---

## 3. Critical Security Rules — Zero Tolerance

### OTP Generation
```python
# ✅ ALWAYS use this
import secrets
otp = secrets.token_digits(6)

# ❌ NEVER use this — not cryptographically secure
import random
otp = str(random.randint(100000, 999999))  # FORBIDDEN
```

### OTP Storage
```python
# ✅ ALWAYS store SHA-256 hash, NEVER the plaintext OTP
import hashlib
otp_hash = hashlib.sha256(otp.encode()).hexdigest()
await cache_service.set(f"otp:{tenant_id}:{phone_hash}", otp_hash, ttl=300)
```

### OTP Enumeration Prevention
```python
# ✅ ALWAYS return 200 for unknown phones (never 404)
# Unknown phone reveals a registered user list via HTTP status codes
return JSONResponse({"status": "new_user"})  # ✅

# ❌ NEVER return 404 for unregistered phone
raise HTTPException(status_code=404)  # FORBIDDEN
```

### JWT Security
```python
# ✅ Algorithm MUST be HS256
jwt.encode(payload, SECRET_KEY, algorithm="HS256")

# ❌ NEVER allow 'none' algorithm or decode without verification
jwt.decode(token, options={"verify_signature": False})  # FORBIDDEN
```

### Customer vs Owner JWT Keys
```python
# ✅ Two SEPARATE signing keys — verified different in config startup
SECRET_KEY = settings.SECRET_KEY               # Owner/Staff JWTs
CUSTOMER_SECRET_KEY = settings.CUSTOMER_SECRET_KEY  # Customer loyalty JWTs
assert SECRET_KEY != CUSTOMER_SECRET_KEY       # Startup validation
```

### PII Encryption
```python
# ✅ ALWAYS encrypt PII with AES-256-GCM, random IV per call
# NEVER store raw phone, email, or name in DB columns
encrypted_phone = encrypt_pii(phone, key=ENCRYPTION_KEY_V1)
phone_hash = hashlib.sha256(phone.encode()).hexdigest()  # searchable index

# ❌ NEVER store plaintext PII
customer.phone = "+919876543210"  # FORBIDDEN
```

### RLS Enforcement
```python
# ✅ ALWAYS set app.tenant_id on every authenticated DB session
# This is done by the SQLAlchemy event listener in db/base.py
# NEVER query without tenant context set

# ❌ NEVER use raw SQL that bypasses RLS
session.execute(text("SET row_security = off"))  # FORBIDDEN
```

### Stripe Webhook
```python
# ✅ ALWAYS verify signature before ANY processing
event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
# Then process event
```

---

## 4. Database Rules

### SQLAlchemy Async Patterns
```python
# ✅ Correct async session usage
async def get_customer(session: AsyncSession, customer_id: UUID) -> Customer:
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    return result.scalar_one_or_none()

# ❌ Never use synchronous session in async context
session.query(Customer).filter(...)  # FORBIDDEN in async code
```

### PostGIS Geofence
```python
# ✅ Always use geography type for distance in metres
from geoalchemy2.functions import ST_DWithin
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

customer_point = from_shape(Point(lng, lat), srid=4326)
result = await session.execute(
    select(Branch).where(
        ST_DWithin(
            Branch.location.cast(Geography),
            customer_point.cast(Geography),
            branch.geofence_radius_m
        )
    )
)
```

### Alembic Migrations
- Every schema change = new migration file. Never edit existing migrations.
- PostGIS-specific: use `op.execute("SELECT AddGeometryColumn(...)")` for geometry columns.
- RLS: always add `op.execute("ALTER TABLE x ENABLE ROW LEVEL SECURITY")` and the policy.

---

## 5. API Design Rules

### Pydantic Schemas First
Define the Pydantic request/response schema in `schemas/` BEFORE writing the route handler.
The schema is the contract. Routes are wired to schemas. Never put validation in route handlers.

### HTTP Status Codes (non-negotiable)
```
201 Created    — POST creates a resource
200 OK         — everything else
400 Bad Request — invalid input caught by Pydantic
401 Unauthorized — invalid/expired JWT
402 Payment Required — plan limit hit → include upgrade_url in response
403 Forbidden  — insufficient role
404 Not Found  — resource not found (NEVER for unregistered OTP phone)
409 Conflict   — duplicate resource
422 Unprocessable Entity — Pydantic validation failure (automatic)
429 Too Many Requests — rate limit hit, always include Retry-After header
503 Service Unavailable — AI provider down
```

### Rate Limiting
Every public endpoint needs a rate limit via SlowAPI:
```python
@limiter.limit("10/minute")
async def my_endpoint(request: Request, ...):
```

### Error Response Format (always consistent)
```json
{
  "error": {
    "code": "OTP_RATE_LIMIT_EXCEEDED",
    "message": "Please wait before requesting another code.",
    "retry_after_seconds": 120
  }
}
```

---

## 6. Celery Task Rules

### Always Use Idempotency Keys
```python
@celery_app.task(bind=True, max_retries=3)
def send_reward_notification(self, customer_id: str, reward_id: str):
    idempotency_key = f"reward:{reward_id}:sent"
    if cache_service.exists(idempotency_key):
        return  # Already sent — idempotent
    # ... send WhatsApp
    cache_service.set(idempotency_key, "1", ttl=86400)
```

### Task Signatures
All Celery tasks must:
- Accept only JSON-serializable arguments (UUIDs as strings, not UUID objects)
- Log task start and end with structlog
- Use `bind=True` + `max_retries=3` for all external API calls
- Set `countdown` on retry to respect external API rate limits

---

## 7. Testing Rules

### What Gets Mocked
```python
# ✅ Always mock external services
@pytest.fixture
def mock_twilio(mocker):
    return mocker.patch("app.services.customer_otp_service.twilio_client.messages.create")

@pytest.fixture
def mock_openai(mocker):
    return mocker.patch("openai.AsyncOpenAI.chat.completions.create")
```

- Twilio, SendGrid, OpenAI, Gemini, Stripe, Google My Business — ALL mocked in tests
- Redis — use real local Redis (fast, no mocking needed)
- PostgreSQL — use separate `quickbite_test` database (set in `TEST_DATABASE_URL` env var)
- Each test runs in a transaction that rolls back after — `@pytest.fixture` with `session.rollback()`

### Test File Naming
```
tests/unit/test_{service_name}.py     — unit tests for services
tests/integration/test_{flow}.py      — full HTTP request→response tests
tests/security/test_{sec_ticket}.py   — Security Dev attack tests
```

### Minimum Test Coverage
- Services: 90% line coverage
- API routes: every status code in acceptance criteria must have a test
- Security: each SEC ticket = dedicated test file

---

## 8. Frontend Rules (Stitch.ai + Animations)

### Generate First, Wire Second
1. Write Stitch.ai prompt (include hex colours, component purpose, key interactions)
2. Generate HTML + Tailwind
3. Export to correct `app/templates/` subfolder
4. Wire Jinja2 `{{ }}` and `{% %}` tags
5. Add Animate.css classes for entrances/errors
6. Add Anime.js timelines for complex interactions
7. Three.js + GSAP only on `landing/index.html`

### Animation Decision — Quick Reference
| Task | Library |
|------|---------|
| Page/card entrance | Animate.css `animate__fadeInUp` |
| OTP error shake | Animate.css `animate__shakeX` |
| Toast slide-in | Animate.css `animate__slideInRight` |
| Upgrade modal entrance | Animate.css `animate__zoomIn` |
| Stamp counter 0→N | Anime.js (targets + update callback) |
| New stamp cell spring | Anime.js `spring(1,80,10,0)` |
| Progress bar fill | Anime.js `easeInOutQuart` |
| Reward unlock timeline | Anime.js `.timeline()` |
| 3D landing hero | Three.js r128 |
| Scroll card reveal | GSAP ScrollTrigger |

### Colour Palette (exact hex — always use these)


---

## 9. Git Workflow Rules
- One branch per ticket: `feature/{ticket-id}` e.g. `feature/loyalty-03-geofence`
- Never commit directly to `main` or `develop`
- Every PR title: `feat(scope): brief description` e.g. `feat(otp): add phone rate limiting`
- CI must pass (bandit + pytest + trivy) before any PR can merge
- Auth/OTP/encryption PRs require Security Dev approval before merge

---

## 10. Environment Variables (never hardcode, always use `settings.`)
```python
# ✅ Always access via settings
from app.core.config import settings
key = settings.SECRET_KEY

# ❌ Never hardcode
key = "my-secret-key"  # FORBIDDEN — will be caught by git-secrets
```

Required env vars (all defined in `.env.example`):
- `SECRET_KEY`, `CUSTOMER_SECRET_KEY` (≥ 64 hex chars each, must be different)
- `ENCRYPTION_KEY_V1` (32 bytes base64 — AES-256-GCM key)
- `DATABASE_URL`, `REDIS_URL`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
- `SENDGRID_API_KEY`, `FROM_EMAIL`
- `OPENAI_API_KEY`, `GOOGLE_AI_API_KEY`
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`


### Restaurant Domain Encryption Rules (add to AGENTS.md Section 3)

# ✅ Staff phone: always hash + encrypt (same pattern as customer.customers)
users.phone_hash = hashlib.sha256(phone.encode()).hexdigest()
users.encrypted_phone = encrypt_pii(phone, key=settings.ENCRYPTION_KEY_V1)

# ✅ GSTIN/PAN: always hash + encrypt (never plaintext in DB)
tenants.gstin_hash = hashlib.sha256(gstin.encode()).hexdigest()
tenants.encrypted_gstin = encrypt_pii(gstin, key=settings.ENCRYPTION_KEY_V1)

# ✅ Branch address: always hash + encrypt
branches.address_hash = hashlib.sha256(normalised_address.encode()).hexdigest()
branches.encrypted_address = encrypt_pii(address, key=settings.ENCRYPTION_KEY_V1)

# ✅ GPS coordinates: PostGIS column ONLY — never store raw lat/lng as separate columns
branches.location = WKTElement(f'POINT({lng} {lat})', srid=4326)  # ONLY this
# ❌ branches.latitude = lat   # FORBIDDEN in v3.1+
# ❌ branches.longitude = lng  # FORBIDDEN in v3.1+

# ✅ IP addresses: always SHA-256 hash before storing — never raw IP in DB
sessions.ip_address_hash = hashlib.sha256(ip.encode()).hexdigest()
# ❌ sessions.ip_address = '192.168.1.1'  # FORBIDDEN in v3.1+

# ✅ The three-tier classification for every new field you add:
# TIER 1 (safe plaintext): public/config data — subdomain, name, role names, status flags
# TIER 2 (hash only): lookup data never shown — phone_hash, ip_address_hash
# TIER 3 (hash + encrypt): lookup AND display needed — email, phone, address, GSTIN
