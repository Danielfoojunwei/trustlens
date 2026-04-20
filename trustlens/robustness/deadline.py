"""Request deadlines.

A `Deadline` is a monotonic budget started at construction. Downstream calls
sample `remaining_ms()` and pass a proportional slice to their own operations.
This prevents one slow oracle from consuming the whole request budget.
"""

from __future__ import annotations

import time


class DeadlineExceeded(Exception):
    """Raised when a deadline elapses before completion."""


class Deadline:
    """Monotonic deadline budget in milliseconds."""

    __slots__ = ("_total_ms", "_start")

    def __init__(self, total_ms: int):
        self._total_ms = int(total_ms)
        self._start = time.monotonic()

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._start) * 1000.0

    def remaining_ms(self) -> float:
        return max(0.0, self._total_ms - self.elapsed_ms())

    def expired(self) -> bool:
        return self.remaining_ms() <= 0

    def check(self) -> None:
        if self.expired():
            raise DeadlineExceeded(f"deadline exceeded ({self._total_ms}ms)")

    def child(self, fraction: float = 0.5, max_ms: int | None = None) -> "Deadline":
        """Create a child deadline consuming at most `fraction` of the remainder.

        Useful when fanning out to N sub-calls: give each one (remaining / N).
        """
        budget = self.remaining_ms() * max(0.0, min(1.0, fraction))
        if max_ms is not None:
            budget = min(budget, max_ms)
        return Deadline(int(budget))
