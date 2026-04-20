"""Per-claim TTL cache for oracle responses.

The cache collapses identical `(oracle_name, claim_text, tenant_id)` lookups
within a TTL window. Different TTLs are appropriate for different claim
classes (biographic facts vs. market prices), so `TTLPolicy` can be swapped.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable, Optional

from cachetools import TTLCache

from trustlens.oracles.base import OracleQuery, OracleResponse


@dataclass
class TTLPolicy:
    """Returns the TTL (seconds) for a given oracle response."""

    default_seconds: int = 900             # 15 minutes
    error_seconds: int = 30                # short cache on errors; retry soon
    high_confidence_seconds: int = 3600    # 1 hour
    high_confidence_threshold: float = 0.9

    def ttl_for(self, response: OracleResponse) -> int:
        if response.error:
            return self.error_seconds
        strength = max(response.support, response.contradiction)
        if strength >= self.high_confidence_threshold:
            return self.high_confidence_seconds
        return self.default_seconds


def _cache_key(oracle_name: str, query: OracleQuery) -> str:
    payload = "\x1f".join((
        oracle_name,
        query.tenant_id or "",
        query.claim_text.strip().lower(),
        query.context.strip().lower(),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class OracleCache:
    """Thread-safe TTL cache keyed on (oracle, tenant, claim, context)."""

    def __init__(self, max_size: int = 10_000, policy: Optional[TTLPolicy] = None):
        self._policy = policy or TTLPolicy()
        # We size by the DEFAULT TTL; per-entry TTL is enforced by expiry metadata.
        self._cache: TTLCache = TTLCache(
            maxsize=max_size,
            ttl=max(
                self._policy.default_seconds,
                self._policy.high_confidence_seconds,
            ),
        )
        self._expires: dict[str, float] = {}

    def get(self, oracle_name: str, query: OracleQuery) -> Optional[OracleResponse]:
        key = _cache_key(oracle_name, query)
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at = self._expires.get(key, 0)
        if time.time() >= expires_at:
            self._cache.pop(key, None)
            self._expires.pop(key, None)
            return None
        cached: OracleResponse = entry
        cached.cache_hit = True
        return cached

    def put(self, oracle_name: str, query: OracleQuery, response: OracleResponse) -> None:
        key = _cache_key(oracle_name, query)
        ttl = self._policy.ttl_for(response)
        self._cache[key] = response
        self._expires[key] = time.time() + ttl

    def stats(self) -> dict:
        return {"size": len(self._cache), "max_size": self._cache.maxsize}

    def clear(self) -> None:
        self._cache.clear()
        self._expires.clear()
