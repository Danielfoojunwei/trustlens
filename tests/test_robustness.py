"""Robustness module tests: circuit breaker, deadlines, shadow sampler."""

from __future__ import annotations

import time

import pytest

from trustlens.robustness.circuit_breaker import CircuitBreaker, CircuitState
from trustlens.robustness.deadline import Deadline, DeadlineExceeded
from trustlens.robustness.shadow_eval import ShadowEvalSampler


def test_circuit_breaker_trips_after_failures() -> None:
    cb = CircuitBreaker(failure_threshold=3, recovery_time_s=0.05)
    assert cb.allow()
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert not cb.allow()
    # After recovery window, half-open probe allowed
    time.sleep(0.06)
    assert cb.allow()
    assert cb.state == CircuitState.HALF_OPEN
    # Success closes
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_deadline_child() -> None:
    d = Deadline(100)
    time.sleep(0.01)
    child = d.child(fraction=0.5)
    assert child.remaining_ms() <= d.remaining_ms() + 1e-3
    assert child.remaining_ms() > 0


def test_deadline_expired() -> None:
    d = Deadline(1)
    time.sleep(0.01)
    assert d.expired()
    with pytest.raises(DeadlineExceeded):
        d.check()


def test_shadow_sampler_is_deterministic() -> None:
    s1 = ShadowEvalSampler(sample_rate=0.5)
    a = s1.should_sample("t1", "req-xyz")
    b = s1.should_sample("t1", "req-xyz")
    assert a == b
    # Near-zero rate should reliably not sample
    s2 = ShadowEvalSampler(sample_rate=0.0)
    assert s2.should_sample("t1", "req-xyz") is False
    s3 = ShadowEvalSampler(sample_rate=1.0)
    assert s3.should_sample("t1", "req-xyz") is True
