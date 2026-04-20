"""Tiny hashing and timestamp helpers used across the codebase.

Consolidated here so every module uses the same canonical spelling. Keeps
``_sha256_hex`` and ``_now_iso`` out of half a dozen modules.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def sha256_hex(data: str | bytes) -> str:
    """SHA-256 hex digest of a string or bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def now_iso_utc() -> str:
    """UTC ISO-8601 timestamp with trailing Z (no microseconds)."""
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
