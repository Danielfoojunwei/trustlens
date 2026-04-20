"""Per-tenant rate limiting and token budgets.

Enforces:
    - request-per-second limits (token-bucket)
    - tokens-per-minute limits (sliding window)

Stateful in-process for this reference build; swap for Redis-backed counters
under a multi-node deployment.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Optional

from trustlens.tenancy.config import TenantConfig


class BudgetExceeded(Exception):
    """Raised when a tenant exceeds its rate/token budget."""

    def __init__(self, kind: str, retry_after_s: float):
        super().__init__(f"budget exceeded: {kind}, retry_after={retry_after_s:.2f}s")
        self.kind = kind
        self.retry_after_s = retry_after_s


@dataclass
class TokenBudget:
    """Token-bucket state for RPS limiting."""
    capacity: float
    refill_rate: float  # tokens per second
    tokens: float
    last_refill: float


class BudgetTracker:
    """Thread-safe per-tenant budget tracking.

    `request(tenant_id, config, tokens_estimate=0)` is called at admission
    time. It raises BudgetExceeded if either the RPS bucket or the tokens/min
    window would be violated.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBudget] = {}
        self._token_windows: dict[str, deque] = {}
        self._lock = Lock()

    def request(
        self, tenant_id: str, config: TenantConfig, tokens_estimate: int = 0
    ) -> None:
        now = time.monotonic()
        with self._lock:
            self._check_rps(tenant_id, config, now)
            if tokens_estimate > 0:
                self._check_tokens(tenant_id, config, tokens_estimate, now)

    def record_tokens_used(
        self, tenant_id: str, tokens_used: int
    ) -> None:
        """Record actual token consumption after a response is known."""
        now = time.monotonic()
        with self._lock:
            window = self._token_windows.setdefault(tenant_id, deque())
            window.append((now, tokens_used))
            self._prune_token_window(window, now)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_rps(
        self, tenant_id: str, config: TenantConfig, now: float
    ) -> None:
        capacity = float(config.max_rps)
        bucket = self._buckets.get(tenant_id)
        if bucket is None:
            bucket = TokenBudget(
                capacity=capacity,
                refill_rate=capacity,  # refill to full in 1 second
                tokens=capacity,
                last_refill=now,
            )
            self._buckets[tenant_id] = bucket

        elapsed = max(0.0, now - bucket.last_refill)
        bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.refill_rate)
        bucket.last_refill = now

        if bucket.tokens < 1.0:
            # How long until one whole token is available?
            needed = 1.0 - bucket.tokens
            retry = needed / bucket.refill_rate if bucket.refill_rate else 1.0
            raise BudgetExceeded("rps", retry)
        bucket.tokens -= 1.0

    def _check_tokens(
        self,
        tenant_id: str,
        config: TenantConfig,
        tokens_estimate: int,
        now: float,
    ) -> None:
        window = self._token_windows.setdefault(tenant_id, deque())
        self._prune_token_window(window, now)
        current = sum(t for _, t in window)
        if current + tokens_estimate > config.max_tokens_per_minute:
            # Retry when the oldest entry falls out of the window
            if window:
                oldest_ts, _ = window[0]
                retry = max(0.0, 60.0 - (now - oldest_ts))
            else:
                retry = 1.0
            raise BudgetExceeded("tokens_per_minute", retry)

    def _prune_token_window(self, window: deque, now: float) -> None:
        cutoff = now - 60.0
        while window and window[0][0] < cutoff:
            window.popleft()
