---
name: customer-otp-flow
description: Complete customer OTP authentication flow — how to implement, wire, and test NEW-OTP-01/02/03
tags: [otp, auth, customer, security]
---

## When to Use This Skill
- Implementing NEW-OTP-01 (OTP request endpoint)
- Implementing NEW-OTP-02 (OTP verify + JWT endpoint)
- Implementing NEW-OTP-03 (Customer registration)
- Debugging OTP delivery or verification issues
- Writing SEC-05 through SEC-10 security audits

## Complete Flow

```
1. Customer enters phone number on STITCH-01 screen
2. POST /api/v1/auth/customer/otp-request
   ├── Validate E.164 format (+91XXXXXXXXXX)
   ├── Check rate limit: 1 request / 2 min per phone (SlowAPI + Redis)
   ├── Check daily limit: max 5 per phone per 24h (Redis counter)
   ├── SHA-256(phone) → lookup in customers table
   │   ├── FOUND: generate OTP, send SMS
   │   └── NOT FOUND: store temp key in Redis → return {status: "new_user"}
   ├── OTP = secrets.token_digits(6)  [NEVER random.randint]
   ├── Store SHA-256(otp) in Redis: key=otp:{tenant_id}:{SHA-256(phone)}, TTL=300
   └── Send via Twilio SMS → return 200 {status: "sent"}

3. Customer enters 6-digit code on STITCH-02 screen
4. POST /api/v1/auth/customer/otp-verify
   ├── Get otp_hash from Redis
   ├── Compare SHA-256(submitted_code) to stored hash
   ├── If wrong: increment attempt counter (max 3)
   │   └── On 3rd failure: DELETE Redis key, return 401 "Too many attempts"
   ├── If expired (key gone): return 400 "Code expired"
   └── If correct:
       ├── Issue Customer JWT (7-day, HttpOnly cookie)
       │   signed with CUSTOMER_SECRET_KEY (≠ SECRET_KEY)
       └── Return 200 with customer info

5. If new user (status was "new_user"):
6. POST /api/v1/customers/register
   ├── Phone pre-filled from Redis temp key
   ├── Accept: name (required), email (optional), whatsapp_opt_in (bool)
   ├── Encrypt all PII: AES-256-GCM, random IV per field
   ├── Store: phone_hash, encrypted_phone, email_hash, encrypted_email, encrypted_name
   ├── Issue Customer JWT immediately (no second OTP needed)
   └── Link any pending anonymous stamp to new customer_id
```

## Key Implementation Details

### Redis Key Patterns
```python
f"otp:{tenant_id}:{hashlib.sha256(phone.encode()).hexdigest()}"  # OTP storage
f"otp_rate:{tenant_id}:{phone_hash}"  # rate limit counter
f"otp_daily:{tenant_id}:{phone_hash}:{today}"  # daily limit counter
f"otp_attempts:{tenant_id}:{phone_hash}"  # verify attempt counter
f"otp_pending:{phone_hash}"  # temp key for new users
```

### Critical Security Constraints
- OTP = `secrets.token_digits(6)` — NEVER `random.randint()`
- Store `SHA-256(otp)` in Redis — NEVER the plaintext OTP
- Unknown phone → `200 {status: "new_user"}` — NEVER `404`
- Attempt counter deleted after 3 wrong attempts → forces new OTP request
- `CUSTOMER_SECRET_KEY` MUST be different from `SECRET_KEY`

### Test Files
- `tests/unit/test_customer_otp.py` — unit tests for OTP service
- `tests/integration/test_otp_flow.py` — full HTTP flow test
- `tests/security/test_otp_enumeration.py` — SEC-06: timing analysis
- `tests/security/test_otp_brute_force.py` — SEC-07: rate limit verification

## Steps
1. Check `app/services/customer_otp_service.py` exists — create if not
2. Implement OTP request logic with all rate limits
3. Implement OTP verify logic with attempt counting
4. Wire to FastAPI routes in `app/api/v1/auth/customer_otp.py`
5. Wire STITCH-01 and STITCH-02 Jinja2 templates to endpoints
6. Run `pytest tests/unit/test_customer_otp.py -v`
7. Run `pytest tests/security/ -v -k "otp"` for security tests
