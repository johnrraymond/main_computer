"""RFC 6238 software authenticator, separate from the durable P-256 identity.

A shared secret is provisioned once and remains private; each displayed code
is valid for a short time window. Never derive this secret from the P-256 key.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import struct
import time

PERIOD = 30
DIGITS = 8
_SECRET_RE = re.compile(r"[A-Z2-7]{52}\Z")  # 256-bit secret, no padding
_CODE_RE = re.compile(r"[0-9]{8}\Z")


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def secret_bytes(secret: str) -> bytes:
    if not isinstance(secret, str) or not _SECRET_RE.fullmatch(secret):
        raise ValueError("INVALID_OTP_SECRET")
    try:
        raw = base64.b32decode(secret + "====", casefold=False)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("INVALID_OTP_SECRET") from exc
    if len(raw) != 32 or base64.b32encode(raw).decode("ascii").rstrip("=") != secret:
        raise ValueError("INVALID_OTP_SECRET")
    return raw


def code_at(secret: str, timestamp: int | float | None = None) -> tuple[str, int]:
    value = time.time() if timestamp is None else timestamp
    if value < 0:
        raise ValueError("INVALID_OTP_TIME")
    counter = int(value // PERIOD)
    digest = hmac.new(secret_bytes(secret), struct.pack(">Q", counter), hashlib.sha256).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset:offset+4])[0] & 0x7fffffff
    return f"{number % (10**DIGITS):0{DIGITS}d}", counter


def valid_counter(secret: str, code: str, now: int | float | None = None) -> int | None:
    if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
        return None
    instant = time.time() if now is None else now
    counter = int(instant // PERIOD)
    # Only one adjacent window for clock skew; per-identity replay fence is
    # enforced by the organization authority's transactional verifier.
    for step in (counter, counter - 1, counter + 1):
        if step >= 0 and hmac.compare_digest(code_at(secret, step * PERIOD)[0], code):
            return step
    return None
