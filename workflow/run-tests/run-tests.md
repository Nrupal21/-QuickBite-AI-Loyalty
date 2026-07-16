---
description: Run QuickBite test suite with coverage report
slash_command: /run-tests
---

## Run Tests

1. Ensure local Docker stack is running // turbo
   `docker-compose up -d`

2. Run the full test suite with coverage
   `pytest --cov=app --cov-report=term-missing --tb=short -q`

3. Check coverage is ≥ 80%
   `pytest --cov=app --cov-fail-under=80`

4. If tests fail, show the failure summary
   `pytest --tb=short -q 2>&1 | tail -30`

## Targeted Test Runs (use when working on a specific ticket)
```bash
# Run only OTP tests
pytest tests/unit/test_customer_otp.py -v

# Run only security tests
pytest tests/security/ -v

# Run only loyalty tests
pytest tests/unit/test_loyalty_service.py -v

# Run integration tests
pytest tests/integration/ -v --tb=long
```
