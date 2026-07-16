---
trigger: always_on
description: Security rules — OTP, JWT, PII, RLS — applied to every task without exception
---

# QuickBite — Security Rules (Non-Negotiable)

## OTP Generation — Zero Tolerance
```python
# ✅ ONLY acceptable
otp = secrets.token_digits(6)

# ❌ BANNED — not cryptographically secure
otp = str(random.randint(100000, 999999))
```
If you see `random.randint` anywhere near OTP code: STOP and flag it.

## OTP Storage — Hash Only
```python
otp_hash = hashlib.sha256(otp.encode()).hexdigest()
cache_service.set(f"otp:{tenant_id}:{phone_hash}", otp_hash, ttl=300)
# NEVER store the plaintext OTP in Redis or any DB
```

## OTP Enumeration — Always 200
```python
# Unknown phone → ALWAYS return 200, never 404
return JSONResponse({"status": "new_user"})  # ✅
raise HTTPException(404)  # ❌ FORBIDDEN
```

## JWT — HS256 Only
```python
jwt.encode(payload, key, algorithm="HS256")  # ✅
# NEVER allow 'none' algorithm or skip verification
```

## Two JWT Keys — Strictly Separate
```python
# Owner/Staff → SECRET_KEY
# Customer loyalty → CUSTOMER_SECRET_KEY
# These MUST be different values. Startup validates this.
```

## PII — Always AES-256-GCM Encrypted
```python
# phone, email, name → always encrypt before storage
encrypted = encrypt_pii(value, key=settings.ENCRYPTION_KEY_V1)
hashed = hashlib.sha256(value.encode()).hexdigest()  # for indexed lookups
# NEVER store raw PII in DB columns
```

## Stripe Webhooks — Always Verify First
```python
event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
# Verify signature BEFORE any DB operation
```

## Auth/OTP/Encryption PRs
Every PR touching auth, OTP, or encryption code MUST be approved by Security Dev before merge.
If you are implementing such a feature, add a note in the PR: "🔐 Security Dev review required"
