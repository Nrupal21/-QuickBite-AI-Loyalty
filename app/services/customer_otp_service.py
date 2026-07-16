"""QuickBite — Customer OTP: generate, Redis store, verify, rate-check.

Uses secrets.token_digits(6) for OTP generation.
Stores SHA-256 hash in Redis with 300s TTL.
Never stores plaintext OTP.
"""
