# QuickBite AI + Loyalty
## Technology Study Notes — Complete Reference

> 24 technologies · What each one is · Why we chose it · How it works · How it fits in this app

---

## Quick Reference

| Category | Technologies | Count |
|----------|-------------|-------|
| ⚙️ Backend | FastAPI, Pydantic v2, SQLAlchemy, Celery, Alembic, Structlog | 6 |
| 🗄️ Database | PostgreSQL + PostGIS, Redis, Asyncpg, GeoAlchemy2 | 4 |
| 🔒 Auth & Security | PyJWT, Passlib+bcrypt, PyOTP, Python secrets, Authlib, SlowAPI, zxcvbn | 7 |
| 🎨 Frontend | Stitch.ai, Jinja2, Tailwind CSS, Three.js, GSAP, **Anime.js**, **Animate.css**, Chart.js | **8** |
| 🤖 AI Providers | OpenAI GPT-4o, Google Gemini 1.5 Pro | 2 |
| 📡 External Services | Twilio, SendGrid, Google My Business API, Stripe, qrcode | 5 |
| 🐳 DevOps | Docker+Compose, GitHub Actions, OpenTelemetry, Sentry, Prometheus+Grafana | 5 |
| **TOTAL** | | **37** |

---

## ⚙️ BACKEND TECHNOLOGIES

---

### B-01 · FastAPI · v0.111 (Python 3.12)

**What is it?**  
FastAPI is a modern Python web framework for building APIs. Built on top of Starlette (async HTTP) and Pydantic (data validation). It generates interactive API documentation automatically.

**Why in QuickBite?**  
Fastest Python framework available. Async-native so it handles hundreds of simultaneous requests without blocking. Auto-generates OpenAPI docs that make testing and AI-coding much easier. Built-in WebSocket support for the live dashboard.

**How it works:**  
When a request comes in (e.g. `POST /api/v1/reviews/generate`), FastAPI: (1) validates the request body against a Pydantic schema, (2) runs any dependency injections (get_current_user, require_role), (3) calls the route handler function, (4) serialises the response to JSON. All steps are async.

**Key patterns in this app:**
```python
# Routers split by domain
app.include_router(auth_router, prefix='/api/v1')

# Dependency injection
@app.post('/reviews/generate')
async def generate_review(
    body: ReviewCreate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    ...

# HTTPException for errors
raise HTTPException(status_code=403, detail='Insufficient permissions')

# WebSocket for live dashboard
@app.websocket('/dashboard/stream')
async def dashboard_stream(websocket: WebSocket): ...
```

**Connects to:** SQLAlchemy (DB calls), Pydantic (validation), SlowAPI (rate limiting), Sentry (error tracking)

---

### B-02 · Pydantic v2 · 2.7

**What is it?**  
Python library that defines data models using Python type hints. Automatically validates incoming data — raising errors with clear messages if data doesn't match the expected shape.

**Why in QuickBite?**  
Every API request is validated before any business logic runs. No bad data ever reaches the database. v2 is 5–50× faster than v1 (compiles validation using Rust).

**How it works:**  
Define a class inheriting from `BaseModel` with typed fields. When request body is parsed, Pydantic checks every field's type, required status, and custom validators.

**Key patterns in this app:**
```python
class OTPRequest(BaseModel):
    phone: str                    # E.164 format validated

class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    tags: list[str] = Field(max_items=5)

class CustomerRegister(BaseModel):
    name: str
    email: EmailStr | None = None
    whatsapp_opt_in: bool = True

@field_validator('phone')
def validate_e164(cls, v):
    # E.164 format check before OTP is sent
    ...
```

**Connects to:** FastAPI (uses Pydantic for request/response), SQLAlchemy (Pydantic models describe DB schemas)

---

### B-03 · SQLAlchemy 2.x Async

**What is it?**  
Python's most popular database toolkit. The ORM (Object-Relational Mapper) lets you work with database tables as Python classes — no raw SQL required. Async version means non-blocking DB queries.

**Why in QuickBite?**  
Async means database queries don't block the FastAPI event loop. While one request waits for the DB, other requests continue processing. Critical for 100+ simultaneous users.

**How it works:**  
Define classes inheriting from `Base`. Each attribute becomes a DB column. The async session (`AsyncSession`) sends queries to PostgreSQL without blocking.

**Key patterns in this app:**
```python
class Customer(Base):
    __tablename__ = 'customers'
    phone_hash = Column(Text, unique=True)
    encrypted_phone = Column(Text, nullable=False)

# Async query
async with get_db() as session:
    result = await session.execute(
        select(Customer).where(Customer.phone_hash == phone_hash)
    )
    customer = result.scalar_one_or_none()

# RLS enforcement via event listener
@event.listens_for(AsyncSession, 'after_begin')
def set_rls(session, transaction, connection):
    connection.execute(text(f"SET app.tenant_id = '{tenant_id}'"))
```

**Connects to:** PostgreSQL (target DB), Alembic (migrations), FastAPI (provides AsyncSession via Depends)

---

### B-04 · Celery 5.3 + celery-beat

**What is it?**  
Distributed task queue. Lets your application hand off long-running jobs to background worker processes so the API can respond immediately instead of waiting.

**Why in QuickBite?**  
GMB review sync (2–10 seconds), AI batch response generation (1–3 seconds per review), SMS campaigns (1000 messages) — all too slow for real-time API response. Celery handles all of these in the background.

**How it works:**  
When code calls a Celery task, it sends a message to Redis (the broker). A separate Celery worker process picks it up and runs it. `celery-beat` triggers periodic tasks on a cron-like schedule.

**Key patterns in this app:**
```python
@celery_app.task(bind=True, max_retries=3)
def sync_gmb_reviews(self, tenant_id):
    ...

# Dispatch without waiting
sync_gmb_reviews.delay(tenant_id)

# Beat schedule
CELERYBEAT_SCHEDULE = {
    'sync-gmb': {
        'task': 'tasks.sync_gmb_reviews',
        'schedule': crontab(minute=0)   # hourly
    }
}
```

**Connects to:** Redis (message broker + result backend), FastAPI (dispatches tasks), Twilio (SMS/WhatsApp tasks), OpenAI (AI batch tasks)

---

### B-05 · Alembic · 1.13

**What is it?**  
Database schema migration tool for SQLAlchemy. Tracks every change to the database structure in versioned files — like Git, but for your database schema.

**Why in QuickBite?**  
Every new table (like `customers` in v2.0) or column change generates a migration file. Applied to any environment in order. Without Alembic, schema changes in production require manual SQL.

**How it works:**  
Each migration file has `upgrade()` (apply the change) and `downgrade()` (roll it back) functions. `alembic upgrade head` applies all pending migrations.

**Key patterns in this app:**
```python
# Auto-generate migration
# alembic revision --autogenerate -m 'add_customers_table'

def upgrade():
    op.create_table('customers',
        sa.Column('phone_hash', sa.Text, unique=True),
        sa.Column('encrypted_phone', sa.Text, nullable=False),
        ...
    )
    # RLS policy in same migration — version-controlled
    op.execute('ALTER TABLE customers ENABLE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY tenant_iso ON customers USING (tenant_id = ...)')
```

**Connects to:** SQLAlchemy (reads models to auto-generate), PostgreSQL (target DB), GitHub Actions (runs migrations in CI before deploy)

---

## 🗄️ DATABASE & CACHE

---

### D-01 · PostgreSQL 16 (via Supabase)

**What is it?**  
World's most advanced open-source relational database. Data stored in structured tables — like spreadsheets but with powerful querying, relationships, and ACID transactions.

**Why in QuickBite?**  
PostgreSQL's **Row-Level Security (RLS)** is central to multi-tenant architecture. Instead of filtering data in application code (which can have bugs), RLS enforces that Restaurant A can NEVER see Restaurant B's data — even if the code has an error.

**How it works:**  
Data stored in 15 tables. Queries use SQL via SQLAlchemy ORM. Transactions ensure related operations either both succeed or both fail. JSONB columns store flexible data like `feature_limits` without requiring schema changes.

**Key patterns in this app:**
```sql
-- RLS policy
CREATE POLICY tenant_iso ON reviews
  USING (tenant_id = current_setting('app.tenant_id')::UUID);

-- JSONB column
feature_limits JSONB → {"branches":3, "ai_responses_pm":1000}

-- UUID primary keys
id UUID PRIMARY KEY DEFAULT gen_random_uuid()
```

**Connects to:** SQLAlchemy (ORM layer), PostGIS (spatial extension), Alembic (schema migrations), Supabase (managed hosting)

---

### D-02 · PostGIS · 3.4

**What is it?**  
Geographic object support extension for PostgreSQL. Stores GPS coordinates, calculates distances between points, checks whether a location is inside a geographic boundary — at the database level.

**Why in QuickBite?**  
Loyalty stamp system needs to verify the customer is actually at the restaurant before awarding a stamp. PostGIS runs the GPS distance calculation inside the database with a spatial index — extremely fast (< 10ms) even with millions of rows.

**How it works:**  
Coordinates stored as `GEOMETRY(POINT, 4326)` — WGS84 coordinate system (what GPS uses). `ST_DWithin` checks if two points are within a given distance. `ST_Distance` calculates exact distance in metres.

**Key patterns in this app:**
```python
from geoalchemy2 import Geometry
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

# Branch model
location = Column(Geometry('POINT', srid=4326))

# Store
branch.location = from_shape(Point(longitude, latitude), srid=4326)

# Geofence check — < 10ms with spatial index
query.filter(Branch.location.ST_DWithin(customer_point, radius))

# Spatial index
# CREATE INDEX idx_branches_location ON branches USING GIST(location)
```

**Connects to:** PostgreSQL (extension), GeoAlchemy2 (Python ORM bindings), geofence_service.py

---

### D-03 · Redis 7 (Upstash)

**What is it?**  
In-memory key-value store. Data held in RAM — reads/writes thousands of times faster than disk-based databases. Supports strings, counters, and expiring keys.

**Why in QuickBite?**  
Redis does **five distinct jobs**: (1) OTP storage with 5-min auto-expiry, (2) JWT revocation list, (3) rate limiting counters, (4) Celery task broker, (5) AI response caching.

**How it works:**  
Data is ephemeral — fine to lose on restart because we rebuild from the database. The `EXPIRE` command sets time-to-live on any key. When TTL reaches zero, Redis automatically deletes the key — this is how OTP auto-expiry works.

**Key patterns in this app:**
```python
# OTP storage (5-min TTL)
redis.set(f"otp:{tenant_id}:{phone_hash}", otp_hash, ex=300)

# Rate limit
count = redis.incr(f"otp_req:{phone}")
redis.expire(f"otp_req:{phone}", 120)
if count > 1:
    raise HTTPException(429)

# JWT blocklist
redis.set(f"revoked:{jti}", 1, ex=remaining_ttl)

# AI cache
cache_key = f"ai:{sha256(restaurant+tags+rating)}"
cached = redis.get(cache_key)
if cached: return cached
```

**Connects to:** Celery (broker), FastAPI (cache + rate limit), customer_otp_service (OTP storage), PyJWT (revocation list)

---

### D-04 · GeoAlchemy2 · 0.14

**What is it?**  
SQLAlchemy extension to understand PostGIS geometry types. Maps Python objects to geographic database columns.

**Why in QuickBite?**  
Without GeoAlchemy2, every geofence query would require writing raw PostGIS SQL strings. GeoAlchemy2 lets us write spatial queries in readable Python code.

**Connects to:** SQLAlchemy (ORM base), PostGIS (spatial functions run inside PostgreSQL), geofence_service.py

---

## 🔒 AUTH & SECURITY

---

### S-01 · PyJWT · 2.8

**What is it?**  
JSON Web Token library. JWT is a compact, self-contained way to represent user identity that can be verified without querying the database on every request.

**Why in QuickBite?**  
After login, QuickBite issues a JWT. For every subsequent request, the client sends this token. FastAPI decodes and verifies it instantly — without a database lookup. The `jti` claim allows specific tokens to be revoked by storing the ID in Redis.

**How it works:**  
A JWT has three parts: **Header** (algorithm type) . **Payload** (claims: user_id, role, tenant_id, expiry, jti) . **Signature** (HMAC-SHA256 of Header+Payload using SECRET_KEY). If anyone tampers with the payload, the signature won't match.

**Key patterns in this app:**
```python
# Issue token
token = jwt.encode({
    'sub': user_id,
    'tenant_id': tenant_id,
    'role': role,
    'jti': str(uuid4()),
    'exp': datetime.utcnow() + timedelta(minutes=15)
}, SECRET_KEY, algorithm='HS256')

# Verify token
payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])

# Customer loyalty JWT (7-day)
customer_token = jwt.encode({
    'sub': customer_id,
    'tenant_id': tenant_id,
    'phone_hash': phone_hash,
    'jti': str(uuid4()),
    'exp': datetime.utcnow() + timedelta(days=7)
}, CUSTOMER_SECRET_KEY, algorithm='HS256')
```

**Connects to:** FastAPI (extracts JWT from Authorization header), Redis (jti revocation list)

---

### S-02 · Passlib + bcrypt · work factor 12

**What is it?**  
Passlib is a Python password hashing library. bcrypt is the specific hashing algorithm. Unlike encryption (reversible), hashing is one-way — you cannot recover the original password from the hash.

**Why in QuickBite?**  
Owner, Manager, and Staff accounts use passwords. These must be stored securely so that even if the database is stolen, the attacker cannot recover them. Work factor 12 = ~250ms per hash, making brute-force impractical.

**Key patterns in this app:**
```python
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')

# Registration
hashed = pwd_context.hash(plain_password)

# Login verification
is_valid = pwd_context.verify(plain_password, hashed_password)
```

**Connects to:** FastAPI auth routes, zxcvbn (password strength checked first)

---

### S-03 · PyOTP · 2.9

**What is it?**  
Implements the TOTP (Time-based One-Time Password) standard (RFC 6238). Generates a 6-digit code that changes every 30 seconds, derived from a secret key and the current time.

**Why in QuickBite?**  
TOTP MFA is enforced for all Owner and Manager accounts. Even if someone steals a password, they still need the 6-digit code from Google Authenticator to log in.

**Key patterns in this app:**
```python
import pyotp

# Generate secret (stored encrypted)
secret = pyotp.random_base32()

# Generate QR code URI for Google Authenticator
totp = pyotp.TOTP(decrypted_secret)
uri = totp.provisioning_uri(email, issuer_name='QuickBite AI')

# Verify submitted code (±30s drift tolerance)
is_valid = totp.verify(submitted_code, valid_window=1)
```

**Connects to:** FastAPI auth routes (MFA verify endpoint), AES-256-GCM encryption (secret stored encrypted)

---

### S-04 · Python secrets (stdlib) ★ KEY FOR OTP

**What is it?**  
Python's built-in module for generating cryptographically secure random numbers. Unlike `random` module (designed for simulations), `secrets` uses the OS's cryptographic random number generator.

**Why in QuickBite?**  
Customer OTP codes **MUST** be cryptographically random. If `random.randint()` were used, codes would be predictable. Research (Zhao et al., 2025) identified exactly this vulnerability in real apps.

**Key patterns in this app:**
```python
import secrets

# OTP generation — cryptographically random
otp = secrets.token_digits(6)    # e.g. '847291'

# Hash for storage
import hashlib
otp_hash = hashlib.sha256(otp.encode()).hexdigest()

# Other uses in QuickBite
branch_token = secrets.token_urlsafe(32)     # Branch QR token
email_token = secrets.token_urlsafe(32)      # Email verification link

# ❌ NEVER use this for OTPs:
# import random; random.randint(100000, 999999)  — predictable!
```

**Connects to:** customer_otp_service.py, Redis (stores SHA-256 hash with TTL), Twilio (sends OTP via SMS)

---

### S-05 · Authlib · 1.3

**What is it?**  
Python library implementing OAuth 2.0 authentication flows. OAuth allows users to grant your app access to their data on another service (like Google) without sharing their password.

**Why in QuickBite?**  
Used for two purposes: (1) 'Sign in with Google' for owners, and (2) connecting Google My Business accounts. PKCE prevents authorization code interception attacks.

**PKCE flow:**
```
1. App generates random code_verifier
2. App sends hash(code_verifier) = code_challenge to Google at auth start
3. Google redirects back with auth code
4. App sends code_verifier — Google verifies hash matches
5. Proves response went to the right app (prevents interception)
```

**Connects to:** Google OAuth API, FastAPI routes (OAuth callback), AES-256-GCM (token storage)

---

### S-06 · SlowAPI · 0.1

**What is it?**  
FastAPI-compatible rate limiting library backed by Redis. Restricts how many requests an IP address or user can make within a time window.

**Why in QuickBite?**  
Without rate limiting, an attacker could try thousands of OTP codes per second. SlowAPI enforces: max 5 login attempts per IP per 15 minutes, max 1 OTP request per phone per 2 minutes, max 5 OTP requests per phone per day.

**Key patterns in this app:**
```python
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)

@app.post('/auth/login')
@limiter.limit('5/15minutes')
async def login(request: Request, ...): ...

@app.post('/auth/customer/otp-request')
@limiter.limit('1/2minutes')    # Per phone number
async def otp_request(request: Request, ...): ...
```

**Connects to:** Redis (stores counters), FastAPI (middleware integration)

---

## 🎨 FRONTEND TECHNOLOGIES

---

### F-01 · Stitch.ai ★ PRIMARY PLATFORM

**What is it?**  
AI-powered frontend development platform. Describe a UI component in plain English → Stitch.ai generates clean, production-ready HTML with Tailwind CSS utility classes.

**Why in QuickBite?**  
QuickBite's entire frontend starts with Stitch.ai prompts. Instead of writing HTML templates manually, a developer describes each screen and Stitch.ai generates it. Reduces frontend dev time by ~60%.

**The workflow:**
```
1. Write prompt: 'Mobile OTP verification. 6 individual 48×56px input boxes. 
                  Teal #0D9488 focus ring. Auto-advance on digit entry.'
2. Generate in Stitch.ai
3. Iterate with follow-up prompts
4. Export to app/templates/customer/otp_verify.html
5. Wire: replace <span>4/10</span> with <span>{{ customer.stamp_count }}/10</span>
```

**Connects to:** Jinja2 (dynamic data injection), Tailwind CSS (design system), FastAPI (serves templates)

---

### F-02 · Jinja2 · 3.1

**What is it?**  
Python's most popular HTML templating engine. Lets you embed Python variables, loops, and conditions directly inside HTML using `{{ }}` and `{% %}` syntax.

**Why in QuickBite?**  
Stitch.ai generates the HTML structure. Jinja2 makes it dynamic by filling in real data from the database at render time.

**Key patterns in this app:**
```python
# FastAPI render
return templates.TemplateResponse('customer/loyalty_card.html', {
    'request': req,
    'customer': customer,
    'branch': branch
})
```
```html
<!-- In template -->
{{ customer.stamp_count }} / 10 stamps

{% for i in range(10) %}
  {% if i < customer.stamp_count %}
    <div class="stamp-filled">⭐</div>
  {% else %}
    <div class="stamp-empty"></div>
  {% endif %}
{% endfor %}
```

---

### F-03 · Tailwind CSS · 3.4

**What is it?**  
CSS framework where you style elements by adding single-purpose utility classes directly to HTML instead of writing separate CSS files.

**Why in QuickBite?**  
Ensures every screen looks consistent. All styling comes from the same set of tokens. Stitch.ai generates Tailwind classes based on the design system in Doc 4. No custom CSS files needed. Tiny production bundle.

**Key patterns in this app:**
```html
<!-- Primary button -->
<button class="bg-[#1A56DB] text-white px-6 py-3 rounded-xl font-semibold 
               hover:bg-[#1648C0] transition-all shadow-md">
  Approve Response
</button>

<!-- OTP input box -->
<input class="w-12 h-14 text-2xl font-bold text-center border-2 border-gray-200 
              rounded-xl focus:border-[#0D9488] focus:ring-2 focus:ring-[#0D9488]/20"
       maxlength="1" inputmode="numeric">

<!-- Loyalty card -->
<div class="bg-gradient-to-br from-[#F0FDF4] to-[#ECFDF5] 
            rounded-2xl border-2 border-[#0D9488] p-6 shadow-lg">
```

---

### F-04 · GSAP + ScrollTrigger · 3.12

**What is it?**  
Industry-standard JavaScript animation library. ScrollTrigger is a GSAP plugin that ties animations to scroll position.

**Why in QuickBite?**  
The landing page uses scroll-driven storytelling — sections pin to viewport, content fades in as you scroll. Individual screen animations (OTP shake, stamp pulse, reward celebration) use GSAP tweens.

**Key patterns in this app:**
```javascript
// Scroll-driven feature reveal
gsap.from('.feature-card', {
    opacity: 0, y: 40, duration: 0.6, stagger: 0.1,
    scrollTrigger: { trigger: '.features', start: 'top 80%' }
});

// OTP wrong code shake
gsap.to('#otp-group', { x: [-6, 6, -6, 6, 0], duration: 0.3 });

// Stamp animation
gsap.to('#stamp-' + count, { scale: 1.3, duration: 0.2, yoyo: true, repeat: 1 });
```

---

### F-05 · Three.js · r128

**What is it?**  
JavaScript library that makes 3D graphics accessible in a web browser using WebGL. Uses the device's GPU for hardware-accelerated 3D rendering.

**Why in QuickBite?**  
The landing page hero features a stylised 3D restaurant/plate model with cel-shading (Ghibli-inspired aesthetic). The model reacts to mouse movement and scroll depth.

**Key patterns in this app:**
```javascript
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(75, w/h, 0.1, 100);
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });

// Cel-shading (cartoon style)
const material = new THREE.MeshToonMaterial({ gradientMap });

// Lerped mouse tracking (smooth)
document.addEventListener('mousemove', e => {
    targetX = e.clientX / window.innerWidth;
});
// In animate loop:
mesh.rotation.y += (targetX * 0.5 - mesh.rotation.y) * 0.05;
```

---

## 🤖 AI PROVIDERS

---

### A-01 · OpenAI GPT-4o

**What is it?**  
OpenAI's most capable multimodal language model. Understands and generates human-like text across a vast range of tasks.

**Why in QuickBite?**  
GPT-4o generates the natural-language review drafts (customer flow) and owner response drafts (dashboard flow). Produces significantly more natural, personalised text than smaller models — critical for reviews that should feel like the customer wrote them.

**How it works:**  
API called via HTTP POST to `/v1/chat/completions`. You send a 'system' message (instructions) and a 'user' message (input). Model responds with generated text. Structured output mode forces valid JSON response.

**Key patterns in this app:**
```python
response = openai.chat.completions.create(
    model='gpt-4o',
    messages=[
        {"role": "system", "content": "You are writing a genuine Google review 
          for {restaurant_name}. Sound like a real customer. Keep it 60-80 words."},
        {"role": "user", "content": f"Rating: 5 stars. Tags: {', '.join(tags)}"}
    ],
    max_tokens=200
)

review_draft = response.choices[0].message.content
```

---

### A-02 · Google Gemini 1.5 Pro

**What is it?**  
Google's multimodal AI model with a very large context window. Competitive performance on language tasks.

**Why in QuickBite?**  
Automatic fallback when OpenAI API goes down, has an outage, or hits rate limits. Controlled by a Redis feature flag — no code deployment needed to switch providers. Also the primary model for Starter tier (lower cost).

**Key patterns in this app:**
```python
# Feature flag controls provider
provider = redis.get('ai_provider') or 'openai'

if provider == 'gemini':
    response = gemini_client.generate_content([prompt])
    draft = response.candidates[0].content.parts[0].text
```

---

## 📡 EXTERNAL SERVICES

---

### E-01 · Twilio · REST API v2010

**What is it?**  
Cloud communications platform providing APIs for sending SMS messages, WhatsApp messages, and voice calls. You call their REST API and they handle global carrier delivery.

**Why in QuickBite?**  
Two critical flows: (1) **OTP delivery via SMS** — the 6-digit code customers receive to log in to their loyalty account, and (2) **WhatsApp reward notifications** — 'Your free coffee is ready!' sent when stamp threshold reached (Pro+ only).

**Key patterns in this app:**
```python
from twilio.rest import Client
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# OTP SMS
message = client.messages.create(
    to=customer_phone,
    from_=TWILIO_FROM_NUMBER,
    body=f'Your QuickBite code: {otp}'
)

# WhatsApp reward notification (Pro+)
client.messages.create(
    to=f'whatsapp:{customer_phone}',
    from_=f'whatsapp:{TWILIO_WHATSAPP_FROM}',
    body=f'Your free coffee is ready! Code: {redemption_code}'
)
```

---

### E-02 · SendGrid · v3 API

**What is it?**  
Email delivery service providing REST API for transactional emails (verification, OTP, password reset) and marketing emails (campaigns).

**Why in QuickBite?**  
Three purposes: (1) **Email OTP delivery** for customers who prefer email login, (2) **account verification emails** for Owner/Manager registration, (3) **monthly reputation report emails** with analytics summaries.

**Key patterns in this app:**
```python
import sendgrid
client = sendgrid.SendGridAPIClient(SENDGRID_API_KEY)

message = Mail(
    from_email=SENDGRID_FROM_EMAIL,
    to_emails=customer_email,
    subject='Your QuickBite login code',
    html_content=f'<p>Your code is: <strong>{otp}</strong> (expires in 5 minutes)</p>'
)
client.send(message)
```

---

### E-03 · Google My Business API · v4.9

**What is it?**  
API that lets businesses programmatically manage their Google listing — fetching reviews and posting replies without logging into Google Maps manually.

**Why in QuickBite?**  
Core integration. Without it, review management would be entirely manual. GMB sync fetches all new reviews every hour. When a Manager approves an AI response, QuickBite posts it directly to Google via the API.

**Key patterns in this app:**
```python
# Fetch new reviews (incremental cursor)
reviews = gmb_client.get(
    f'/accounts/{account_id}/locations/{location_id}/reviews',
    params={'pageToken': gmb_sync_cursor}
)

# Post approved response
gmb_client.post(
    f'/accounts/{account_id}/locations/{location_id}/reviews/{review_id}/reply',
    json={'comment': approved_response_text}
)
```

---

### E-04 · Stripe · 2024-04-10

**What is it?**  
Leading payments infrastructure platform. Handles subscription billing, trials, plan upgrades, payment method management, invoicing, and failed payment recovery.

**Why in QuickBite?**  
QuickBite's monetisation runs entirely through Stripe. When a restaurant upgrades from Starter to Pro, Stripe handles the payment, sends webhooks to update subscription status in our database, and handles the 14-day trial period.

**Key patterns in this app:**
```python
import stripe
stripe.api_key = STRIPE_SECRET_KEY

# Create checkout session for plan upgrade
session = stripe.checkout.Session.create(
    price=STRIPE_PRICE_PRO_MONTHLY,
    mode='subscription',
    trial_period_days=14,
    success_url=f'{FRONTEND_BASE_URL}/dashboard?upgraded=true',
    cancel_url=f'{FRONTEND_BASE_URL}/billing',
)

# Webhook verification
event = stripe.Webhook.construct_event(
    payload, sig_header, STRIPE_WEBHOOK_SECRET
)
if event['type'] == 'invoice.payment_failed':
    # Start grace period
    ...
```

---

### E-05 · qrcode (Python) · 7.4

**What is it?**  
Python library that generates QR code images. A QR code is a 2D barcode encoding a URL or text string as black and white squares that any phone camera can decode instantly.

**Why in QuickBite?**  
Each restaurant branch has its own unique QR code encoding a URL like `https://qb.ai/q/{branch_token}`. The `branch_token` is a cryptographically secure random string that routes to the correct restaurant's review + loyalty page.

**Key patterns in this app:**
```python
import qrcode
from io import BytesIO

qr = qrcode.QRCode(
    error_correction=qrcode.ERROR_CORRECT_M,  # 15% damage tolerance
    box_size=10,
    border=4
)
qr.add_data(f'https://qb.ai/q/{branch.qr_code_token}')
img = qr.make_image(fill_color='#1E2A3A', back_color='white')

# Upload to Cloudflare R2
buffer = BytesIO()
img.save(buffer, format='PNG')
r2.put_object(Body=buffer.getvalue(), Key=f'qr/{branch.id}.png')
```

---

## 🐳 DEVOPS & OBSERVABILITY

---

### V-01 · Docker + Docker Compose · 26.x

**What is it?**  
Docker packages an application and all its dependencies into a 'container' — an isolated, reproducible environment. Docker Compose defines a multi-container application in a single YAML file.

**Why in QuickBite?**  
Eliminates 'it works on my machine' problems. One `docker-compose up` command starts the entire QuickBite stack — FastAPI, PostgreSQL, Redis, Celery, and monitoring tools.

**Key patterns in this app:**
```dockerfile
# Dockerfile
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir -r requirements/prod.txt
COPY ./app /app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
```yaml
# docker-compose.yml
services:
  api:
    build: .
    depends_on: [postgres, redis]
  postgres:
    image: postgis/postgis:16-3.4   # PostgreSQL + PostGIS
  redis:
    image: redis:7-alpine
  celery_worker:
    command: celery -A app.workers.celery_app worker
  celery_beat:
    command: celery -A app.workers.celery_app beat
```

---

### V-02 · GitHub Actions

**What is it?**  
Automation platform built into GitHub. Runs workflows in YAML files whenever code events occur. Used for CI (testing + linting) and CD (deploying to staging/production).

**Why in QuickBite?**  
Every code change goes through a mandatory CI pipeline before being merged. Catches bugs, security issues, and broken tests before they reach production.

**Key patterns in this app:**
```yaml
# ci.yml
on: push:
  branches: [main]

jobs:
  test:
    steps:
      - uses: actions/checkout@v4
      - run: pip install -r requirements/dev.txt
      - run: ruff check app/                    # linting
      - run: bandit -r app/                     # security scan
      - run: pytest --cov=app --cov-fail-under=80  # 80% coverage gate
      - run: trivy image quickbite:latest       # Docker vuln scan
      - run: alembic upgrade head               # DB migration check
```

---

### V-03 · OpenTelemetry · 1.x

**What is it?**  
Open-source observability framework that automatically tracks requests as they flow through your system — from HTTP request, through service calls, into the database, into Celery tasks, and out to external APIs.

**Why in QuickBite?**  
When a customer submits a review, the request goes through FastAPI → AI engine → database → Celery → GMB API. If something is slow or fails, OpenTelemetry shows exactly which step took how long.

**Key patterns in this app:**
```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span('generate_ai_review') as span:
    span.set_attribute('model', 'gpt-4o')
    span.set_attribute('tenant_id', tenant_id)
    draft = ai_engine.generate(prompt)

# trace_id automatically propagated across FastAPI → Celery
# Every log line includes trace_id:
logger.info('OTP verified', trace_id=trace_id, tenant_id=tenant_id)
```

---

### V-04 · Sentry · 2.x

**What is it?**  
Error monitoring platform. When an unhandled exception occurs, Sentry captures it with full context (stack trace, user info, request data) and sends an alert.

**Why in QuickBite?**  
Without Sentry, the first you'd know about a production bug is a customer complaint. Sentry catches every unhandled exception the moment it happens — with the tenant_id, user_id, and full stack trace.

**Key patterns in this app:**
```python
import sentry_sdk

sentry_sdk.init(
    dsn=SENTRY_DSN,
    traces_sample_rate=0.1,
    before_send=scrub_pii    # removes encrypted_email, phone from error context
)

# Every error includes:
# - tenant_id (from JWT)
# - user_id (from JWT)  
# - trace_id (from OpenTelemetry)
# - full request context
```

---

### V-05 · Prometheus + Grafana

**What is it?**  
Prometheus collects numeric metrics from your application. Grafana visualises them as dashboards with charts and alerts.

**Why in QuickBite?**  
Answers questions like: 'How many OTP requests per minute?', 'What's the P95 response time?', 'How many Celery tasks are queued?'

**Key patterns in this app:**
```python
from prometheus_client import Counter, Histogram

otp_requests = Counter('otp_requests_total', 'OTP requests', ['status', 'channel'])
otp_requests.labels(status='success', channel='sms').inc()

ai_latency = Histogram('ai_generation_seconds', 'AI latency',
                        buckets=[0.1, 0.3, 0.5, 1.0, 3.0])

# Grafana alert: if p95(ai_generation_seconds) > 3.0 for 5min → Slack alert
```

---

---

### F-06 · Anime.js · 3.2.1 ★ NEW

**What is it?**
Anime.js is a lightweight JavaScript animation engine (~14KB). It animates CSS properties, SVG, DOM attributes, and plain JavaScript objects. It supports timelines, stagger delays, easing functions including spring physics, and per-target delays.

**Why in QuickBite?**
QuickBite uses Anime.js for every complex, sequenced, multi-step animation on customer and dashboard screens — things that require JavaScript timing control. The reward unlock sequence has 5 steps in sequence. The stamp counter increments from old to new value. The progress bar fills to the new percentage. The star rating cascades left to right with a stagger. Animate.css handles simple entrance/exit animations — Anime.js handles everything that needs precise JS orchestration.

**How it works:**
`anime()` takes a `targets` selector and a set of properties to animate to. Timelines chain animations using `.add()` with optional overlap offsets (`'-=100'` means start 100ms before previous ends). The `stagger` helper creates per-element delays automatically. Spring easing `spring(mass, stiffness, damping, velocity)` creates organic animations that overshoot and settle.

**Key patterns in this app:**
```javascript
// Stamp counter increment
anime({
  targets: { val: oldCount },
  val: newCount,
  round: 1,
  duration: 600,
  easing: 'easeOutQuart',
  update: (anim) => {
    document.querySelector('#stamp-count').innerHTML =
      Math.round(anim.animations[0].currentValue) + '/10';
  }
});

// New stamp cell — spring physics bounce
anime({
  targets: '#stamp-cell-' + stampIndex,
  scale: [0, 1.2, 1.0],
  opacity: [0, 1],
  duration: 500,
  easing: 'spring(1, 80, 10, 0)',
  delay: stampIndex * 50   // stagger: 50ms per cell
});

// Reward unlock — 5-step timeline
const tl = anime.timeline({ easing: 'easeOutExpo' });
tl.add({ targets: '#reward-overlay', opacity: [0,1], duration: 400 })
  .add({ targets: '#reward-text', translateY: [30,0], opacity: [0,1], duration: 500 }, '-=100')
  .add({ targets: '#redemption-code', scale: [0.8,1], opacity: [0,1] }, '-=200');

// Star rating cascade (left to right)
anime({
  targets: '.star-icon',
  color: (el, i) => i < rating ? '#FF6B35' : '#E5E7EB',
  scale: (el, i) => i < rating ? [1, 1.3, 1] : 1,
  delay: anime.stagger(60),
  easing: 'easeOutBack'
});
```

**Connects to:** Stitch.ai (added manually to exported HTML), Animate.css (complementary — Animate.css for CSS-only, Anime.js for JS-controlled — they never overlap), Three.js (GSAP controls Three.js camera on landing page, not Anime.js)

---

### F-07 · Animate.css · 4.1.1 ★ NEW

**What is it?**
Animate.css is a library of 80+ ready-to-use, cross-browser CSS animations. You activate them by adding class names to HTML elements — no JavaScript required. Classes like `animate__animated animate__fadeInUp`, `animate__shakeX`, `animate__bounceIn`, `animate__pulse` cover entrances, attention seekers, and exits.

**Why in QuickBite?**
QuickBite uses Animate.css for all simple, single-shot animations that do not require JavaScript control: page and card entrance animations when content first appears (`fadeInUp`), OTP error shake (`shakeX` — replaces custom `@keyframes`), toast notifications sliding in (`slideInRight`), success confirmation bouncing in (`bounceIn`), the upgrade modal zooming in (`zoomIn`), and loading state pulsing (`pulse infinite`). Loaded in `<head>` so it is available before the first render.

**How it works:**
Add two classes to any element: `animate__animated` (always required) + the specific animation name. The animation plays once automatically. For repeating, add `animate__infinite`. For speed: `animate__fast` (0.8s), `animate__faster` (0.5s), `animate__slow` (2s). Override duration with CSS variable `--animate-duration`.

To re-trigger an animation (e.g. wrong OTP entered twice), remove the classes, force a reflow with `void el.offsetWidth`, then re-add.

**Key patterns in this app:**
```javascript
// Card entrance — added in Stitch.ai export or on DOMContentLoaded
element.classList.add('animate__animated', 'animate__fadeInUp');

// OTP error shake — re-triggerable
function triggerOTPError(el) {
  el.classList.remove('animate__animated', 'animate__shakeX');
  void el.offsetWidth;  // force reflow
  el.classList.add('animate__animated', 'animate__shakeX');
}

// Toast with auto-dismiss via Anime.js
const toast = document.createElement('div');
toast.className = 'toast animate__animated animate__slideInRight animate__faster';
document.body.appendChild(toast);
// Anime.js handles the 3s delay then fade-out removal

// Custom duration via CSS variable
element.style.setProperty('--animate-duration', '0.4s');
element.classList.add('animate__animated', 'animate__zoomIn');

// Loading pulse — infinite until data arrives
skeleton.classList.add('animate__animated', 'animate__pulse', 'animate__infinite');
// On data loaded:
skeleton.classList.remove('animate__animated', 'animate__pulse', 'animate__infinite');
```

**Connects to:** Stitch.ai (classes added to exported HTML or via JS classList), Anime.js (Animate.css for CSS-only animations; Anime.js for JS-controlled — zero overlap by design), Three.js (Animate.css not used on landing page — Three.js and GSAP own that page entirely)

---

## How All Technologies Work Together

A single customer QR scan touches almost every technology in the stack:

| Step | Technology | Action |
|------|-----------|--------|
| 1 | Browser + Stitch.ai | Stitch.ai-generated HTML renders in browser. Tailwind styles applied. |
| 2 | FastAPI middleware | Subdomain resolved → tenant_id injected into request.state. |
| 3 | Jinja2 + Tailwind | Loyalty card page renders with dynamic customer data. |
| 4 | FastAPI + SlowAPI | OTP requested. Redis rate counter checked (1 req / 2 min). |
| 5 | Python secrets | `secrets.token_digits(6)` generates cryptographically secure OTP. |
| 6 | Redis | `SET otp:{tenant}:{phone_hash} {otp_hash} EX 300` — auto-expires in 5 min. |
| 7 | Twilio | HTTP POST to Twilio API → SMS delivered to customer. |
| 8 | FastAPI + PyJWT | OTP verified. Redis hash comparison. Customer JWT (7-day) issued. |
| 9 | PostGIS + GeoAlchemy2 | `ST_DWithin(branch.location, customer_point, radius)` — < 10ms. |
| 10 | PostgreSQL + RLS | `INSERT INTO stamp_logs`. RLS ensures tenant isolation. |
| 11 | GSAP + canvas-confetti | Stamp grid updates. GSAP animates cell. Confetti burst fires. |
| 12 | Celery + Redis | Reward check → Celery task dispatched via Redis broker. |
| 13 | Twilio WhatsApp | Celery worker calls Twilio WhatsApp API. Reward notification sent. |
| 14 | OpenTelemetry + Sentry | Full trace_id on every step. Sentry captures any unhandled exception. |
| 15 | Prometheus + Grafana | `loyalty_scans_total` counter incremented. Dashboard shows real-time data. |

> ✅ **Every technology in this app has a specific job. None of them overlap. Understanding what each one does and why it was chosen is the foundation of being able to build, debug, and extend this system.**

---

*QuickBite AI + Loyalty · Technology Study Notes · Reference Document · Confidential*
