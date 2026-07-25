"""Hashing for User.pin_hash — see the docstring on app/models.py:User for
what this PIN is (and isn't) for.

Stdlib-only on purpose (no passlib/bcrypt dependency) — a 4-digit PIN has
only 10,000 possible values, so hash cost buys negligible real protection
against brute force; the actual defense is rate limiting
(app/routers/captive.py's _RateLimiter), not a slow hash. PBKDF2-SHA256
with a per-user random salt is still used instead of storing PINs in the
clear, mainly so a DB dump/leak doesn't hand out everyone's PIN directly.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_ITERATIONS = 100_000


def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), _ITERATIONS).hex()
    return f"{salt}${digest}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), _ITERATIONS).hex()
    return hmac.compare_digest(candidate, digest)


def is_valid_pin_format(pin: str) -> bool:
    return len(pin) == 4 and pin.isdigit()
