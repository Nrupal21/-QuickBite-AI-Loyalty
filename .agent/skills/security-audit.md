---
name: security-audit
description: How to conduct and document a SEC ticket audit — what to check, how to test, how to document findings
tags: [security, audit, otp, jwt, rls, pen-test]
---

## When to Use This Skill
- Any SEC-01 to SEC-30 ticket implementation
- Security Dev audit sessions
- Reviewing a PR that touches auth, OTP, or encryption code

## Audit Framework

### Phase 1: Static Code Review (SEC-03 to SEC-16)
```bash
# Step 1: Search for forbidden patterns
grep -r "random.randint" app/          # OTP generation — must be 0 results
grep -r "random.random" app/           # Same
grep -r "alg.*none" app/               # JWT algorithm — must be 0 results
grep -r "logger.*phone" app/           # PII in logs — must be 0 results
grep -r "logger.*email" app/           # PII in logs — must be 0 results
grep -r "hardcoded_secret" app/        # git-secrets catches most, manual check too

# Step 2: Verify correct patterns exist
grep -r "secrets.token_digits" app/services/customer_otp_service.py  # must exist
grep -r "SHA-256\|hashlib.sha256" app/services/customer_otp_service.py  # must exist
grep -r "construct_event" app/api/v1/billing/  # Stripe webhook verification

# Step 3: Check bcrypt work factor
grep -r "CryptContext\|rounds" app/core/security.py  # must show rounds=12
```

### Phase 2: Database Security Tests (SEC-04, SEC-23)
```sql
-- Run these directly in psql as Tenant A user
-- ALL queries must return 0 rows from Tenant B

SET app.tenant_id = 'tenant-a-uuid';
SELECT COUNT(*) FROM users;           -- should return only Tenant A count
SELECT COUNT(*) FROM customer_reviews; -- should return only Tenant A reviews
SELECT COUNT(*) FROM stamp_logs;      -- etc.
SELECT COUNT(*) FROM customers;
SELECT COUNT(*) FROM stamp_logs WHERE tenant_id != 'tenant-a-uuid'; -- must be 0
```

### Phase 3: Runtime Attack Tests (SEC-18 to SEC-22)
```python
# SEC-18: JWT 'none' algorithm attack
import base64, json
header = base64.urlsafe_b64encode(json.dumps({"alg":"none","typ":"JWT"}).encode()).decode()
payload = base64.urlsafe_b64encode(json.dumps({"sub":"user-id","tenant_id":"abc"}).encode()).decode()
forged_token = f"{header}.{payload}."  # no signature
response = client.get("/api/v1/dashboard/stats",
                      headers={"Authorization": f"Bearer {forged_token}"})
assert response.status_code == 401, "CRITICAL: JWT none-alg attack succeeded!"

# SEC-19: OTP brute force lockout
for i in range(3):
    response = client.post("/api/v1/auth/customer/otp-verify",
                           json={"phone": "+919876543210", "code": "000000"})
assert response.status_code == 401
assert "Too many attempts" in response.json()["error"]["message"]
# Verify Redis key is deleted
assert not redis_client.exists(f"otp:{tenant_id}:{phone_hash}")

# SEC-20: SQL injection via review tags
response = client.post("/api/v1/reviews/generate",
                       json={"qr_token": "test", "rating": 5,
                             "tags": ["'; DROP TABLE customer_reviews; --"]})
assert response.status_code in [200, 422]  # either OK or validation error
# Verify table still exists
assert session.execute(text("SELECT 1 FROM customer_reviews LIMIT 1"))
```

### Phase 4: Findings Documentation Format (SEC-24)
```markdown
## SEC-24-001: OTP Brute Force — HIGH

**Title:** OTP verify endpoint allows per-IP brute force
**Severity:** HIGH
**CVSS Score:** 7.5 (AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N)
**Endpoint:** POST /api/v1/auth/customer/otp-verify
**Test:** Sent 200 OTP verify attempts from 10 different IPs in 1 minute.
         Per-phone limit (3 attempts) enforced, but per-IP limit not enforced.
**Evidence:** Screenshot of 200 successful attempts via Burp Suite Intruder
**Remediation:** Add SlowAPI rate limit: 5 OTP verify attempts per IP per hour
**Status:** OPEN → assigned to Backend Dev 1
**Fixed in PR:** #87 (after fix, re-test shows IP limit now enforced)
**Re-tested by:** Security Dev, 2025-01-16
**Final Status:** FIXED ✓
```

## SEC Ticket Checklist
Before marking any SEC ticket complete:
- [ ] Static code check performed
- [ ] Automated test written and passing
- [ ] Finding documented (if applicable)
- [ ] Fixed PR linked (if a finding was made)
- [ ] Re-test performed after fix
- [ ] Result added to SEC-24 findings report
