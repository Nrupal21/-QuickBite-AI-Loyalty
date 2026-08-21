# Testing Patterns

**Analysis Date:** 2026-08-19

## Test Framework

**Runner:**
- Framework: Pytest 7.x
- Async mode: auto (via `asyncio_mode = "auto"` in pyproject.toml)
- Config file: `pyproject.toml` [tool.pytest.ini_options]
- Important: `-p no:langsmith_plugin` flag in addopts (avoid plugin conflict with pydantic/3.12)

**Run Commands:**
```bash
# All tests
pytest tests/unit/ -v

# Specific test file
pytest tests/unit/test_auth_login.py -v

# Specific test function
pytest tests/unit/test_auth_login.py::test_login_correct_password_no_mfa_returns_tokens -v

# Watch mode (requires pytest-watch)
ptw tests/unit/

# Coverage report
pytest --cov=app --cov-report=term-missing tests/

# Check linting before tests
ruff check app/
```

**Key Options:**
- `-v` — verbose output (test names)
- `-p no:langsmith_plugin` — required (see pyproject.toml addopts)
- `--strict-markers` — registered markers only
- Registered markers: `asyncio` (mark test as async), `allow_smtp` (opt out of SMTP block)

## Test File Organization

**Location:** `tests/unit/test_*.py` (co-located with source, not mirroring directory structure)

**Naming:**
- `test_*.py` for test files
- `test_{domain}_{criterion}.py` or `test_{service_name}.py`
- Examples: `test_auth_login.py`, `test_response_service.py`, `test_customer_otp.py`

**File Structure:**
```
tests/unit/test_auth_login.py
├── Module docstring (purpose + mocking notes)
├── Test data constants (TENANT_ID, EMAIL, PASSWORD, etc)
├── Fixtures (autouse fixtures first, then named)
├── Helper functions (make_session, make_user, added_instances)
├── Test classes (if used) — usually not
└── Test functions (test_* grouped by feature)
```

## Test Structure

**One Acceptance Criterion = One Test:**

```python
@pytest.mark.asyncio
async def test_login_correct_password_no_mfa_returns_tokens(mocker):
    """AUTH-02 criterion: correct password + no MFA returns access + refresh tokens."""
    # Arrange
    user = make_user(mfa_enabled=False)
    role = make_role()
    session = make_session([user, role])
    mocker.patch(
        "app.services.auth_service.create_refresh_token", return_value="refresh-token-abc"
    )
    
    # Act
    response = await AuthService(session=session).login(
        UserLogin(identifier=EMAIL, password=PASSWORD), "iphash"
    )
    
    # Assert
    assert response.access_token
    assert response.refresh_token == "refresh-token-abc"
    assert response.role == "STAFF"
    assert response.tenant_id == str(TENANT_ID)
    audit_rows = added_instances(session, AuditLog)
    assert audit_rows[-1].action == "login_success"
```

**Arrange-Act-Assert (AAA):**
- Arrange: set up test data, mocks, fixtures
- Act: call the function under test
- Assert: verify outcomes, side effects, state changes

**Test Naming:** `test_{function}_{scenario}_{expected_outcome}`
- `test_login_correct_password_no_mfa_returns_tokens`
- `test_login_tenth_failure_locks_account_and_sends_email`
- `test_request_otp_unregistered_identifier_returns_new_user_not_404`

## Fixture Patterns

**Shared Fixtures:** `tests/conftest.py`
```python
@pytest.fixture
async def client():
    """Async test client for FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.fixture(autouse=True)
def block_smtp(request, mocker):
    """Safety net: no real SMTP in tests. Opt out with @pytest.mark.allow_smtp"""
    if "allow_smtp" in request.keywords:
        return
    mocker.patch("app.core.email_transport.aiosmtplib.send", new=AsyncMock())
```

**Per-File Fixtures:**
```python
@pytest.fixture(autouse=True)
def resolved_tenant(mocker):
    """Stub the credential->tenant resolvers and the RLS bind.
    
    Both issue their own session.execute, so stubbing them keeps
    execute_results lists describing only the ORM queries this test
    reasons about.
    """
    mocker.patch(
        "app.services.auth_service.bootstrap.tenant_for_user_email_hash",
        AsyncMock(return_value=TENANT_ID),
    )
    mocker.patch(
        "app.services.auth_service.bootstrap.tenant_for_user_username_hash",
        AsyncMock(return_value=TENANT_ID),
    )
    mocker.patch("app.services.auth_service.rls.set_tenant_context", AsyncMock())
```

**Autouse Fixtures:**
- Run before every test unless opted out
- Used for universal setup: SMTP block, RLS stubbing, feature flags
- Mark with `autouse=True` and document what they mock

## Helper Functions (Factories)

**Pattern:** Functions that construct mocked ORM instances and session stubs

```python
def make_session(execute_results: list) -> MagicMock:
    """Stub session with execute() side effects returning the given ORM instances."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    results = []
    for value in execute_results:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)
    session.execute = AsyncMock(side_effect=results)
    return session

def make_user(**overrides) -> User:
    """Construct a test User instance with reasonable defaults."""
    defaults = {
        "tenant_id": TENANT_ID,
        "role_id": ROLE_ID,
        "email_hash": sha256_hex(EMAIL),
        "encrypted_email": encrypt_pii(EMAIL),
        "hashed_password": hash_password(PASSWORD),
        "mfa_enabled": False,
        "is_active": True,
        "failed_login_count": 0,
        "locked_until": None,
    }
    defaults.update(overrides)
    user = User(**defaults)
    user.id = uuid.uuid4()  # Set explicitly since instances aren't persisted
    return user

def added_instances(session: MagicMock, model: type) -> list:
    """Extract all instances of a given model added to the session."""
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]
```

**Usage:**
- Call factory functions in Arrange phase
- Override defaults with kwargs: `make_user(mfa_enabled=True, failed_login_count=9)`
- Extract instances after commit: `audit_rows = added_instances(session, AuditLog)`

## Mocking

**What to Mock (External):**
```python
# Always mock external services
mocker.patch("app.services.auth_service.messaging_service.send_verification_email", AsyncMock(return_value=True))
mocker.patch("app.services.customer_otp_service.cache_service.set", AsyncMock())
mocker.patch("app.services.response_service.ai_engine.generate_response_draft", AsyncMock(return_value=...))
mocker.patch("app.services.gmb_service.google_my_business.reviews.list", AsyncMock())
```

- Twilio, SendGrid, OpenAI, Gemini, Stripe, Google My Business — ALWAYS mocked
- Email transport: mocked in `block_smtp` autouse fixture
- Redis/cache_service: can be mocked per test if it doesn't boot local Redis (see response_service tests)

**What NOT to Mock:**
```python
# Use real local instances where practical
# Redis: use real local Redis for speed/simplicity (tests that need a real instance)
# PostgreSQL: use separate `quickbite_test` database (set TEST_DATABASE_URL env var)
# DB session rollback: use session rollback per-transaction, not mocking
```

**Mock Configuration:**
```python
# Simple return value
mocker.patch("module.function", return_value="expected")

# Async return value
mocker.patch("module.function", AsyncMock(return_value=response_obj))

# Side effects (for multiple calls)
session.execute = AsyncMock(side_effect=[result1, result2, result3])

# Verify calls
send_email.assert_awaited_once()
send_email.assert_awaited_with("email@example.com", "body")
cache_set.await_args.args[0]  # first positional arg of last call
cache_set.await_args_list[0]  # first call in the list
```

## Error Testing

**Pattern: Exceptions are assertions**

```python
@pytest.mark.asyncio
async def test_login_wrong_password_returns_401_generic():
    user = make_user()
    session = make_session([user])
    
    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).login(
            UserLogin(identifier=EMAIL, password="wrong"), "iphash"
        )
    
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["code"] == "INVALID_CREDENTIALS"
```

**For every error path:**
- Assert the HTTPException status code
- Assert the error code string
- Assert the message or optional fields (retry_after, suggestions)
- Assert side effects (incremented counter, logged event)

## State & Audit Testing

**Pattern: Check both mutation and audit trail**

```python
@pytest.mark.asyncio
async def test_login_tenth_failure_locks_account_and_sends_email(mocker):
    user = make_user(failed_login_count=9)
    session = make_session([user])
    send_email = mocker.patch(
        "app.services.auth_service.messaging_service.send_account_locked_email",
        AsyncMock(return_value=True),
    )
    
    with pytest.raises(HTTPException) as exc_info:
        await AuthService(session=session).login(
            UserLogin(identifier=EMAIL, password="wrong"), "iphash"
        )
    
    # Assert state change
    assert user.failed_login_count == 10
    assert user.locked_until > datetime.now(timezone.utc)
    
    # Assert side effect (email sent)
    send_email.assert_awaited_once()
    
    # Assert audit trail
    audit_rows = added_instances(session, AuditLog)
    assert audit_rows[-1].action == "account_locked"
```

**Verify:**
- ORM instance mutations (`user.failed_login_count == 10`)
- Async service calls (`.assert_awaited_once()`, `.assert_awaited_with(...)`)
- Audit log entries (check last action via `added_instances`)

## Async Testing

**Pattern: @pytest.mark.asyncio decorator**

```python
@pytest.mark.asyncio
async def test_register_valid_returns_verification_email_sent(mocker):
    session = make_session([None])
    send_email = mocker.patch(
        "app.services.auth_service.messaging_service.send_verification_email",
        AsyncMock(return_value=True),
    )
    
    response = await AuthService(session=session).register(make_request(), BASE_URL)
    
    assert response.status == "verification_email_sent"
    send_email.assert_awaited_once()
```

**Rules:**
- Every async function test needs `@pytest.mark.asyncio`
- Use `AsyncMock` for mocking async functions
- Await calls: `await AuthService(...).login(...)`
- Assert on async calls: `.assert_awaited()`, `.assert_awaited_once()`, `.await_args`

## Parametrized Tests

**Pattern: Test multiple scenarios with one test function**

```python
@pytest.mark.parametrize("identifier,expected_code", [
    ("marco.p", "username_hash"),
    ("owner@marcos.in", "email_hash"),
])
@pytest.mark.asyncio
async def test_login_accepts_email_and_username(mocker, identifier, expected_code):
    # ... test code that uses identifier, expected_code
```

**Use when:**
- Same logic, different inputs
- Multiple valid identifier types
- Testing boundary conditions (empty, max length, special chars)

## Coverage

**Current Target:** No strict enforcement in CI, but track via:
```bash
pytest --cov=app --cov-report=term-missing --cov-report=html
```

**View HTML Report:**
```bash
open htmlcov/index.html
```

**Focus Areas:**
- Services (business logic): 90%+ coverage
- Routes (thin wrappers): every status code tested
- Security/auth (session management): comprehensive — every state machine path
- Data models: fixtures cover ORM field combinations

**Gaps to avoid:**
- Untested error paths (every HTTPException should have a test)
- Untested state transitions (happy path + all edge cases)
- Untested side effects (audit logs, emails, webhooks, broadcasts)

## Test Isolation & Transactions

**Pattern: Database rollback per test**

```python
# conftest.py sets up a test database and rolls back after each test
# so tests don't leak state to each other.
# Tests run in separate transactions.
```

**For async tests:**
- Session fixtures handle rollback automatically
- Tests that directly access the database get a fresh transaction
- Mock sessions (like in auth tests) have no state carry-over

## Integration vs. Unit Tests

**Unit Tests:** `tests/unit/test_*.py`
- Mock database (mocked session with side effects)
- Mock external services (OpenAI, Twilio, Stripe)
- Mock Redis (if needed)
- Test service/business logic in isolation
- Fast (< 1s per test)

**Integration Tests:** Not yet implemented in this codebase
- Would use real database + real Redis
- Would test full HTTP flow: route → service → database
- Would mock only external APIs (OpenAI, Twilio)
- Slower but verify end-to-end

**Pattern in this project:** Primarily unit tests with mocked dependencies

## Test Data & Constants

**Per-file test constants:**
```python
TENANT_ID = uuid.uuid4()
ROLE_ID = uuid.uuid4()
EMAIL = "owner@marcos.in"
PASSWORD = "korma-monsoon-49-bicycle"
PHONE = "+919876543210"
OTP_CODE = "482913"
BASE_URL = "http://test/api/v1/auth/verify-email"
```

**Factory functions:**
- Create ORM instances with reasonable defaults
- Override specific fields for the test scenario
- Always set `.id` explicitly (instances aren't persisted, so identity isn't auto-generated)

## Disabling/Skipping Tests

**Pattern:**
```python
# Skip a single test
@pytest.mark.skip(reason="TODO: implement geofence validation")
async def test_geofence_check_within_radius():
    pass

# Conditionally skip
@pytest.mark.skipif(not settings.OPENAI_API_KEY, reason="OpenAI not configured")
async def test_ai_response_draft():
    pass

# Mark as expected to fail
@pytest.mark.xfail(reason="LOYALTY-03: geofence fraud detection not implemented")
async def test_geofence_fraud_detection():
    pass
```

**Best practice:** Don't skip — implement with mocks or mark `xfail` with a ticket ID

## Common Test Patterns

### RLS Binding Test
```python
@pytest.mark.asyncio
async def test_login_binds_tenant_before_reading_the_user_row(mocker):
    """RLS policies require tenant_id to be bound before the row is readable."""
    user = make_user(mfa_enabled=False)
    session = make_session([user, make_role()])
    set_tenant = mocker.patch("app.services.auth_service.rls.set_tenant_context", AsyncMock())
    
    await AuthService(session=session).login(UserLogin(...), "iphash")
    
    set_tenant.assert_awaited()
    assert set_tenant.await_args.args[1] == TENANT_ID
```

### Enumeration Oracle Prevention Test
```python
@pytest.mark.asyncio
async def test_request_otp_unregistered_identifier_returns_new_user_not_404(mocker):
    """Unknown phone returns 200 with new_user status, never 404.
    
    A 404 would reveal that an identifier is not registered, breaking
    phone enumeration resistance.
    """
    session = make_session([None])
    response = await customer_otp_service.request_otp(
        OTPRequest(identifier=PHONE, tenant_id=TENANT_ID), session
    )
    
    assert isinstance(response, OTPNewUserResponse)
    assert response.status == "new_user"
```

### Idempotency Test
```python
@pytest.mark.asyncio
async def test_post_approved_response_idempotent_via_redis_flag(mocker):
    """Multiple calls with same review_response_id only post once."""
    # First call posts and sets flag
    response = ReviewResponse(...)
    mocker.patch("cache_service.exists", AsyncMock(return_value=False))
    cache_set = mocker.patch("cache_service.set", AsyncMock())
    
    await post_approved_response_to_gmb(response)
    
    cache_set.assert_awaited_once()
    idempotency_key = cache_set.await_args.args[0]
    assert idempotency_key == f"gmb_post:{response.id}"
```

---

*Testing analysis: 2026-08-19*
