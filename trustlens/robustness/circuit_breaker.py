"""Circuit breaker.

Classic three-state breaker:
    CLOSED    — normal operation, failures counted
    OPEN      — tripped, all calls rejected for `recovery_time_s`
    HALF_OPEN — single probe call allowed; success closes, failure reopens

Used by the verifier service to shed load when oracles or downstream LLMs
cascade failures, and by the gateway to isolate sick upstream backends.
"""

from __future__ import annotations

import time
from enum import Enum
from threading import Lock


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe circuit breaker."""

    def __init__(
        self,
        failure_threshold: int = 10,
        recovery_time_s: float = 30.0,
        half_open_probe_limit: int = 1,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_time_s = recovery_time_s
        self._half_open_probe_limit = half_open_probe_limit

        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float = 0.0
        self._half_open_probes = 0
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def allow(self) -> bool:
        """Return True if a call is currently permitted."""
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self._recovery_time_s:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_probes = 0
                else:
                    return False
            # HALF_OPEN
            if self._half_open_probes < self._half_open_probe_limit:
                self._half_open_probes += 1
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            if self._state != CircuitState.CLOSED:
                self._state = CircuitState.CLOSED
                self._half_open_probes = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == CircuitState.HALF_OPEN:
                self._trip()
                return
            if self._failures >= self._failure_threshold:
                self._trip()

    def _trip(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._half_open_probes = 0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self._state.value,
                "failures": self._failures,
                "opened_at": self._opened_at,
                "failure_threshold": self._failure_threshold,
                "recovery_time_s": self._recovery_time_s,
            }
