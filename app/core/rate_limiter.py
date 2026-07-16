"""QuickBite — SlowAPI rate limiting: IP + tenant + OTP-specific limits.

Every public endpoint needs a rate limit. One shared Limiter instance so
main.py registers a single exception handler; OTP endpoints add stricter
per-phone limits on top (NEW-OTP-01).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
