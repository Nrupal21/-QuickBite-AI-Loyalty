---
trigger: model_decision
description: Activate when writing tests, pytest fixtures, or implementing test coverage
---

# QuickBite — Testing Rules

## External Services — Always Mock
```python
@pytest.fixture
def mock_twilio(mocker):
    """Mock Twilio SMS — NEVER make real API calls in tests"""
    return mocker.patch(
        "app.services.customer_otp_service.twilio_client.messages.create",
        return_value=MagicMock(sid="SM1234567890", status="queued")
    )

@pytest.fixture
def mock_openai(mocker):
    return mocker.patch(
        "openai.AsyncOpenAI.chat.completions.create",
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="Great food!"))])
    )
```

Mock everything: Twilio, SendGrid, OpenAI, Gemini, Stripe, Google My Business, R2.
Never mock: Redis (use real local), PostgreSQL (use `quickbite_test` DB).

## Transaction Rollback Fixture
```python
@pytest.fixture
async def session(engine):
    """Each test runs in a transaction that auto-rolls back — no test pollution"""
    async with engine.begin() as conn:
        await conn.execute(text(f"SET app.tenant_id = '{TEST_TENANT_ID}'"))
        async with AsyncSession(bind=conn) as session:
            yield session
            await session.rollback()
```

## Test Structure — One Test per Acceptance Criterion
```python
class TestCustomerOTPRequest:
    """Tests for NEW-OTP-01: Customer OTP Request endpoint"""

    async def test_registered_phone_returns_200_sent(self, client, mock_twilio):
        """AC: Registered phone → 200 {status: sent}"""
        response = await client.post("/api/v1/auth/customer/otp-request",
                                     json={"phone": "+919876543210"})
        assert response.status_code == 200
        assert response.json()["status"] == "sent"
        mock_twilio.assert_called_once()

    async def test_unregistered_phone_returns_200_new_user(self, client, mock_twilio):
        """AC: Unregistered phone → 200 {status: new_user} NOT 404"""
        response = await client.post("/api/v1/auth/customer/otp-request",
                                     json={"phone": "+919999999999"})
        assert response.status_code == 200      # never 404
        assert response.json()["status"] == "new_user"

    async def test_second_request_within_2_min_returns_429(self, client):
        """AC: 2nd request within 2 minutes → 429"""
        await client.post("/api/v1/auth/customer/otp-request",
                          json={"phone": "+919876543210"})
        response = await client.post("/api/v1/auth/customer/otp-request",
                                     json={"phone": "+919876543210"})
        assert response.status_code == 429
        assert "Retry-After" in response.headers
```

## Security Test Pattern (for SEC tickets)
```python
class TestOTPEnumeration:
    """SEC-06: OTP enumeration prevention"""

    async def test_timing_difference_below_50ms(self, client):
        """Registered vs unregistered response time < 50ms (no timing oracle)"""
        import time
        start = time.monotonic()
        await client.post("/api/v1/auth/customer/otp-request",
                          json={"phone": "+919876543210"})  # registered
        registered_time = time.monotonic() - start

        start = time.monotonic()
        await client.post("/api/v1/auth/customer/otp-request",
                          json={"phone": "+910000000000"})  # unregistered
        unregistered_time = time.monotonic() - start

        assert abs(registered_time - unregistered_time) < 0.05  # < 50ms
```

## Coverage Requirements
- Services: ≥ 90% line coverage
- Routes: every HTTP status code in Doc 5 acceptance criteria must have a test
- Security: each SEC ticket has a dedicated file in `tests/security/`

## Run Commands
```bash
pytest tests/unit/test_customer_otp.py -v          # specific file
pytest tests/ -v --tb=short                         # all tests
pytest --cov=app --cov-report=term-missing           # with coverage
pytest tests/security/ -v -k "otp"                  # security tests
```
