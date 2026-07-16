---
description: Complete pre-PR check — ruff + bandit + full tests + coverage. Run before every PR.
slash_command: /pre-pr-check
---

## Complete Pre-PR Quality Gate

### Step 1: Lint (ruff)
`ruff check app/ && echo "✅ Lint passed" || echo "❌ Lint failed"`

### Step 2: Format check
`ruff format --check app/ && echo "✅ Format OK" || echo "❌ Run: ruff format app/"`

### Step 3: Security scan (bandit)
`bandit -r app/ -ll -q && echo "✅ Bandit passed" || echo "❌ Security issues found"`

### Step 4: Full test suite with coverage
`pytest --cov=app --cov-fail-under=80 --tb=short -q`

### Step 5: Dependency CVE check
`safety check --short-report`

### Step 6: Summary
If all 5 steps pass:
```
✅ Pre-PR check PASSED
   - Lint: ✓
   - Security scan: ✓
   - Tests: ✓ (coverage ≥ 80%)
   - Dependencies: ✓
Safe to open PR. Remember:
  - If this PR touches auth/OTP/encryption: add "🔐 Security Dev review required" to PR description
  - Branch name matches ticket ID: feature/{ticket-id}
  - PR title format: feat(scope): description
```

If any step fails:
```
❌ Pre-PR check FAILED
Fix the issues above before opening a PR.
```
