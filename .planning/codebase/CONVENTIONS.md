# Coding Conventions

**Analysis Date:** 2026-08-19

## Naming Patterns

**Files:**
- Snake case: `auth_service.py`, `customer_otp_service.py`, `review_service.py`
- Models: singular/descriptive: `user.py`, `customer.py`, `tenant.py`, `audit.py`
- Test files: `test_` prefix with matching service/module name: `test_auth_login.py`, `test_response_service.py`
- Routers: domain-based: `auth.py`, `customers.py`, `billing.py`, `reviews.py`

**Functions & Methods:**
- Snake case: `create_access_token()`, `verify_password_constant_time()`, `generate_redemption_code()`
- Async: `async def` for database operations, external API calls, and I/O: `async def register()`, `async def login()`
- Private helpers: prefix `_`: `_utcnow()`, `_get_user_by_identifier_hash()`, `_noop_admin_bypass()`
- Module-level helpers: `_check_password_strength()`, `_ip_hash()`, `_require_pending()`

**Variables:**
- Snake case: `access_token`, `refresh_token`, `tenant_id`, `email_hash`
- Constants: UPPER_SNAKE_CASE: `PENDING_REGISTRATION_TTL_SECONDS`, `MIN_ZXCVBN_SCORE`, `MFA_MAX_ATTEMPTS`
- Factory/builder kwargs: match ORM field names for clarity: `make_user(tenant_id=..., role_id=..., email_hash=...)`

**Types & Classes:**
- PascalCase: `User`, `Role`, `Customer`, `AuthService`, `Principal`
- Enums: PascalCase class, uppercase members: `class AuthProvider(str, Enum): LOCAL = "local"`
- Dataclasses: frozen + slots for immutability: `@dataclass(frozen=True, slots=True)`

## Code Style

**Formatting:**
- Tool: Ruff (`ruff check app/`, `ruff format app/`)
- Line length: 120 characters
- Configuration: `pyproject.toml` [tool.ruff]

**Linting:**
- Tool: Ruff
- Rules: E, F, I, W (pycodestyle errors/warnings, Pyflakes, isort, whitespace)
- isort plugin: `known-first-party = ["app"]` for import grouping

**Type Hints:**
- **Always complete** — every function signature must include parameter types and return type
```python
# ✅ Correct
async def login(
    self, 
    request: UserLogin, 
    ip_hash: str
) -> TokenResponse | MFAChallengeResponse | MFAEnrollmentRequiredResponse:

# ❌ Forbidden — no bare dict/list returns
async def login(self, request, ip_hash):
    return {...}
```
- Use `|` union syntax (PEP 604), not `Union`
- Use `str | None` over `Optional[str]`

## Import Organization

**Order (3 groups, 1 blank line between):**
```python
# Group 1: Python standard library
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

# Group 2: Third-party packages
import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Group 3: Local application (app.*)
from app.core import cache_service
from app.core.config import settings
from app.core.encryption import decrypt_pii, encrypt_pii, sha256_hex
from app.core.security import hash_password, verify_password_constant_time
from app.db import bootstrap, rls
from app.db.models.user import Role, User
from app.schemas.auth import UserLogin, UserRegister
from app.services.auth_service import AuthService
```

**Path Aliases:**
- First-party only: `from app.* import ...`
- No relative imports (`from . import` or `from ..core import`)
- Modules explicitly imported for re-export: `from app.services import auth_service, messaging_service`

## Module Documentation

**Required at file top:**
- Module docstring explaining purpose and scope
- Ticket references (AGENTS.md section numbers, feature flags, schema names)
- Special considerations (RLS, encryption, async patterns)

```python
"""QuickBite — Auth services: register, login, MFA, refresh, logout.

AUTH-01 implements registration + email verification. AUTH-02 (login + TOTP MFA)
and AUTH-03 (refresh rotation + logout) handle the second factor. AUTH-04 adds
MFA enrollment when roles require it.

RLS note: by the time a route reaches here, `get_current_user` has already
bound `app.tenant_id` on the session, so every query below is tenant-scoped
by Postgres itself, not by an extra WHERE clause.
"""
```

## Function & Class Documentation

**Docstrings:**
- Triple-quoted strings, placed immediately after `def`/`class`
- Short summary (one line) for simple functions
- Multi-line for complex logic, parameters with defaults, state changes

```python
def verify_password_constant_time(password: str, hashed: str | None) -> bool:
    """Like verify_password, but always pays the bcrypt cost even when `hashed`
    is None (unknown email) — prevents a timing oracle revealing whether an
    email is registered."""
    matches = _pwd_context.verify(password, hashed or _DUMMY_PASSWORD_HASH)
    return matches if hashed is not None else False
```

- For acceptance criterion functions: state the criterion explicitly
```python
@pytest.mark.asyncio
async def test_login_tenth_failure_locks_account_and_sends_email(mocker):
    """AUTH-02 criterion: failing 10 times locks the account and sends an email."""
```

## Error Handling

**Pattern:**
```python
raise HTTPException(
    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    detail={
        "error": {
            "code": "PASSWORD_TOO_WEAK",
            "message": "Please choose a stronger password.",
            "suggestions": suggestions,  # optional field, context-dependent
        }
    },
)
```

**Rules:**
- Always use `status.HTTP_*` constants from `fastapi`, never raw integers
- Error detail always has `error` key with `code` and `message`
- Code: UPPER_SNAKE_CASE, specific identifier (not generic "ERROR")
- Message: human-readable, not a log level or internal detail
- Optional context fields (retry_after_seconds, suggestions, upgrade_url) only when relevant
- HTTP status codes per AGENTS.md Section 5:
  - 201 Created — POST creates resource
  - 200 OK — everything else
  - 400 Bad Request — invalid input caught by Pydantic
  - 401 Unauthorized — invalid/expired JWT
  - 402 Payment Required — plan limit hit
  - 403 Forbidden — insufficient role, deactivated account, email not verified
  - 404 Not Found — resource not found (NEVER 404 for unregistered OTP phone)
  - 409 Conflict — duplicate resource
  - 422 Unprocessable Entity — Pydantic validation failure
  - 423 Locked — account locked
  - 429 Too Many Requests — rate limit (always include Retry-After header)
  - 503 Service Unavailable — AI provider/external service down

## Logging

**Framework:** Structlog

**Import:**
```python
import structlog
logger = structlog.get_logger(__name__)
```

**Pattern:**
```python
# Log event as string (not f-string), context as key-value pairs
logger.info(
    "review_response.approved",
    review_response_id=str(response.id),
    tenant_id=str(response.tenant_id),
    approved_by=str(current_user.id),
)

# Warning/error
logger.warning("feature_flag.lookup_failed", flag=flag, error=str(exc))
logger.error("email.timeout", timeout_seconds=settings.SMTP_TIMEOUT_SECONDS)
```

**Rules:**
- Event name format: `domain.operation` (e.g., `login_success`, `review_response.approved`, `billing.projection.drained`)
- Always serialize UUIDs/enums to strings: `str(uuid_value)`
- NEVER log PII: phone numbers, emails, passwords, SSNs, tokens
- NEVER include raw request bodies or response payloads — use event name + specific fields
- Async operations: log start on `.info()`, completion/errors at appropriate levels

## Schema (Pydantic) Design

**File location:** `app/schemas/` (one file per domain: `auth.py`, `customers.py`, `billing.py`)

**Class naming:**
- Request models: end with `Request` (e.g., `UserRegister`, `OTPVerify`, `BecomeRestaurantRequest`)
- Response models: end with `Response` (e.g., `TokenResponse`, `RegisterResponse`)
- DTOs for internal use: descriptive names (e.g., `MeResponse`, `CustomerProfileResponse`)

**Validation:**
```python
class UserRegister(BaseModel):
    email: EmailStr  # Pydantic built-in EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    username: str | None = Field(default=None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_.]+$")
```

- Use Pydantic validators for complex rules
- Field descriptions for API docs: not typically used (test criterion expectations live in AGENTS.md)
- Config: use model_config for schema_extra, frozen models when needed (immutability)

## Service Layer Design

**Location:** `app/services/` (one file per domain)

**Function/method signatures:**
- Async for database + external API: `async def register(...) -> RegisterResponse:`
- Session always injected: `session: AsyncSession` parameter
- Return schemas, not ORM models
- Raise HTTPException for domain errors, let data layer exceptions bubble

```python
async def register(
    self, 
    request: UserRegister, 
    verification_base_url: str
) -> RegisterResponse:
    """Validates password strength, checks uniqueness, queues verification email."""
    self._check_password_strength(request.password)
    # ... db operations
```

- No logging in service layer — let caller/worker log the decision
- Pure business logic: no request/response handling, no context assumptions

## Route Handlers

**Location:** `app/api/v1/routers/`

**Pattern:**
```python
@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def login(
    request: Request,
    payload: UserLogin,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse | MFAChallengeResponse:
    return await AuthService(session=session).login(payload, _ip_hash(request))
```

- Thin handlers: validate schema (Pydantic), call service, return schema
- Every public endpoint has `@limiter.limit("N/period")`
- `Request` parameter for client IP, headers: `request: Request`
- Extract IP hash once per handler: `_ip_hash(request: Request) -> str`
- Log at boundary (route entry/exit) if needed — service layer stays log-free

## Model Design

**Location:** `app/db/models/` (one file per domain)

**Patterns:**
- Inherit from `Base` (SQLAlchemy declarative)
- Use `Mapped` + `mapped_column` (SQLAlchemy 2.x syntax)
- TIER classification comment for PII fields
```python
class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "restaurant"}  # tenant-scoped
    
    # TIER 1 (plaintext): public data, no encryption
    name: Mapped[str] = mapped_column(String)
    
    # TIER 2 (hash only): lookup field, never displayed
    ip_address_hash: Mapped[str] = mapped_column(String)
    
    # TIER 3 (hash + encrypt): lookup AND display
    email_hash: Mapped[str] = mapped_column(String, unique=True)
    encrypted_email: Mapped[str] = mapped_column(String)
```

- PostGIS geometry columns: `from geoalchemy2 import Geometry`
- JSONB for semi-structured: `from sqlalchemy.dialects.postgresql import JSONB`
- Soft deletes: `is_active: Mapped[bool]` with default True, checked in routes
- Audit trail: FK to `User.id`, never delete users (breaks audit logs)

## Rate Limiting

**Framework:** SlowAPI

**Every public endpoint** requires:
```python
from app.core.rate_limiter import limiter

@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, ...):
```

- Format: `"N/period"` (e.g., "5/minute", "100/hour")
- Low limits for auth endpoints: 5-10/minute
- Higher for reads: 30-100/minute
- Return 429 with Retry-After header (automatic via SlowAPI)

## Encryption & Hashing

**PII Handling:**
```python
from app.core.encryption import encrypt_pii, decrypt_pii, sha256_hex

# TIER 3: hash for lookup + encrypt for display
phone_hash = sha256_hex(phone)
encrypted_phone = encrypt_pii(phone)

# TIER 2: hash only, never decrypt
ip_hash = sha256_hex(client_ip)
```

- Always hash before database lookup (searchable index)
- Always encrypt before storage (AES-256-GCM)
- Never store raw PII columns
- Use `secrets` module for OTP/token generation: `secrets.token_digits(6)`, never `random`

## Comments & Inline Documentation

**When to Comment:**
- Why, not what: "prevents a timing oracle" beats "checks password"
- Security decisions: "constant-time comparison to prevent timing attacks"
- Non-obvious business rules: "tenant_id is None for standard users until they register a restaurant"
- TODO with ticket: `# TODO(AUTH-04): add rate limiting after enrollment`

**Avoid:**
- Obvious comments: `x += 1  # increment x`
- Future work without tickets: `# maybe add caching later`
- Redundant with test names: test name already states criterion

## Testing Comments

**Module docstring required:**
```python
"""Unit tests for AuthService.login — one per AUTH-02 login acceptance criterion.

DB session and Redis are mocked per AGENTS.md testing rules.
"""
```

- One test per criterion, test name states criterion
- Comments explain non-obvious mocking setup
- No comments inside assertions — test name + assertions speak for themselves

---

*Convention analysis: 2026-08-19*
