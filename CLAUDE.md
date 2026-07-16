# QuickBite AI + Loyalty — Claude Code Rules
# File: CLAUDE.md (project root) — read by Claude Code on every session start
# Global override: ~/.claude/CLAUDE.md (user-level defaults)

## Read First
@AGENTS.md

The AGENTS.md file above contains all shared rules. This file adds Claude Code-specific
behaviour on top of those shared rules.

---

## Claude Code Session Startup Checklist

Before writing any code, confirm:
1. Which ticket am I implementing? (reference Doc 5 acceptance criteria)
2. What branch am I on? Should be `feature/{ticket-id}` — never `main` or `develop`
3. Have I read the relevant section of Doc 2 (architecture) for this feature area?
4. Does this touch auth, OTP, or encryption? → Security Dev review required on PR

---

## Claude Code — Preferred Workflow

### Step 1: Plan before coding
When given a ticket, output a brief plan:
```
Ticket: LOYALTY-03
Files to create/modify:
  - app/api/v1/loyalty/scan.py        (new route)
  - app/services/geofence_service.py  (new service)
  - app/schemas/loyalty.py            (add ScanRequest + ScanResponse)
  - app/db/models/stamp_log.py        (already exists — add is_fraudulent field)
  - tests/unit/test_geofence.py       (new test file)
Dependencies: LOYALTY-02 must be done (branch + QR exists)
Security risk: YES — geofence fraud possible — mention to Security Dev
```
Then ask: "Shall I proceed?"

### Step 2: Write schemas first
Always write the Pydantic schema before the route:
```python
# schemas/loyalty.py
class ScanRequest(BaseModel):
    qr_token: str
    gps_lat: float = Field(ge=-90, le=90)
    gps_lng: float = Field(ge=-180, le=180)

class ScanResponse(BaseModel):
    stamp_count: int
    reward_progress: float
    reward_unlocked: bool
    redemption_code: str | None = None
```

### Step 3: Write the service
Business logic goes in `app/services/`, never in route handlers.

### Step 4: Write the route
Route handler is thin — calls service, returns schema.

### Step 5: Write tests
One test per acceptance criterion from Doc 5.

---

## Claude Code — Code Style Preferences

### Imports
```python
# Group 1: stdlib
import hashlib, secrets
from datetime import datetime, timezone
from uuid import UUID

# Group 2: third-party
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

# Group 3: local
from app.core.dependencies import get_current_customer
from app.db.base import get_db
from app.services.loyalty_service import LoyaltyService
```

### Type hints — always complete
```python
# ✅ Full type hints
async def scan_loyalty(
    request: ScanRequest,
    session: AsyncSession = Depends(get_db),
    current_customer: Customer = Depends(get_current_customer_optional),
) -> ScanResponse:

# ❌ No bare dict returns, no missing return types
async def scan_loyalty(request, session, customer):  # FORBIDDEN
```

### Structlog — always use
```python
import structlog
logger = structlog.get_logger(__name__)

# In service/route:
logger.info("loyalty.scan.success",
    customer_id=str(customer_id),
    branch_id=str(branch_id),
    stamp_count=stamp_count,
    # NEVER log phone numbers, emails, or any PII
)
```

### Error handling
```python
# ✅ Specific exceptions with codes
raise HTTPException(
    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    detail={
        "error": {
            "code": "GEOFENCE_RATE_LIMIT",
            "message": "Already collected your stamp today at this location.",
            "retry_after_seconds": 3600
        }
    },
    headers={"Retry-After": "3600"}
)
```

---

## Claude Code — File Creation Rules

When creating a new file:
1. Check it doesn't already exist: `ls app/services/`
2. Follow the exact folder structure from AGENTS.md Section 2
3. Include module docstring explaining what the file does
4. Never create `utils.py` — be specific: `otp_utils.py`, `encryption_utils.py`

When creating a new model:
```python
# app/db/models/stamp_log.py
"""
StampLog model — records every loyalty scan attempt (valid and fraudulent).
RLS-protected: tenant_id column enforced via policy.
"""
from app.db.base import Base

class StampLog(Base):
    __tablename__ = "stamp_logs"
    # ... always include tenant_id and RLS policy in migration
```

---

## Claude Code — What NOT to Do

- ❌ Don't use `asyncio.run()` inside FastAPI — already in async context
- ❌ Don't use `time.sleep()` — use `await asyncio.sleep()`
- ❌ Don't import from `app.main` in tests — circular imports
- ❌ Don't write TODO comments without a ticket ID: `# TODO(LOYALTY-03): ...`
- ❌ Don't use f-strings in SQL: `f"SELECT * WHERE id = {id}"` — SQL injection risk
- ❌ Don't commit `.env` files or any secrets
- ❌ Don't add new Python packages without checking if they're in requirements first
- ❌ Don't skip the test for any acceptance criterion

---

## Claude Code — Multi-File Tasks (Agentic Mode)

When implementing a full ticket (e.g. LOYALTY-03), Claude Code will:
1. Create/edit all files listed in the plan
2. Run `ruff check app/` and fix any lint errors
3. Run `pytest tests/unit/test_geofence.py -v` to verify tests pass
4. Output a summary: files changed, tests added, acceptance criteria met

If any test fails:
1. Diagnose the failure
2. Fix the root cause
3. Re-run tests
4. If still failing after 2 iterations, stop and explain the blocker

---

## Claude Code — Memory Instructions

At the start of each session:
- Remember: this is a multi-tenant SaaS — EVERY DB query must be scoped to `tenant_id` via RLS
- Remember: customer OTP uses `secrets.token_digits(6)` — never suggest `random.randint()`
- Remember: Two separate JWT keys — `SECRET_KEY` (owners) ≠ `CUSTOMER_SECRET_KEY` (customers)
- Remember: All PII (phone, email, name) must be AES-256-GCM encrypted before DB storage

---

## Claude Code — Useful Commands Reference

```bash
# Start dev environment
docker-compose up -d

# Run tests for your specific ticket area
pytest tests/unit/test_customer_otp.py -v --tb=short

# Run full suite before PR
pytest --cov=app --cov-report=term-missing

# Lint check (must pass before commit)
ruff check app/

# Security scan (run locally before push)
bandit -r app/ -ll

# Generate new Alembic migration
alembic revision --autogenerate -m "add_stamp_logs_is_fraudulent"

# Apply migrations
alembic upgrade head

# Seed development data
python scripts/seed_roles.py && python scripts/seed_plans.py
```

---

## Claude Code — CLAUDE.local.md (Personal Overrides)

Create `CLAUDE.local.md` (gitignored) for personal preferences that shouldn't be shared:
```markdown
# My local overrides
- Use port 8001 (8000 is taken by another project on my machine)
- My local Redis is on port 6380
- Skip docker-compose for DB — I use a local PostgreSQL install
```

---

## Claude Code — PR Description Format

When Claude Code finishes a task and you open a PR, use:
```markdown
## Ticket
Closes #{TICKET-ID} — {Ticket Name}

## What Changed
- [list of files changed with 1-line description each]

## Acceptance Criteria
- [x] Each criterion from Doc 5, checked off

## Security Review Needed
YES / NO — explain if YES (any auth, OTP, encryption, or RLS changes)

## How to Test
1. docker-compose up -d
2. pytest tests/unit/test_{area}.py -v
3. [Manual test steps if needed]
```
