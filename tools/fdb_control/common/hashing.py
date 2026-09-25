from __future__ import annotations

import hashlib

from .models import ContentHash


def sha256_bytes(payload: bytes) -> ContentHash:
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    return ContentHash(algorithm="sha256", digest=hashlib.sha256(payload).hexdigest())
