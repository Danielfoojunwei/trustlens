"""Prometheus metrics.

All metrics are per-process and scraped via `/metrics`. Histogram buckets
are tuned for the latency SLOs published in the SLA plan (80ms/180ms/400ms
Deep Inspector envelope).
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    Gauge,
    generate_latest,
)


_LATENCY_BUCKETS_MS = (
    1, 5, 10, 25, 50, 80, 100, 180, 250, 400, 600, 900,
    1_500, 2_500, 5_000, 10_000,
)


class Metrics:
    """Holds the Prometheus registry and all metric handles."""

    def __init__(self, registry: CollectorRegistry | None = None):
        self.registry = registry or CollectorRegistry()

        self.verify_requests_total = Counter(
            "trustlens_verify_requests_total",
            "Total /v1/verify requests",
            ["tenant", "status"],
            registry=self.registry,
        )
        self.verify_latency_ms = Histogram(
            "trustlens_verify_latency_ms",
            "Verification pipeline latency (ms)",
            ["tenant"],
            buckets=_LATENCY_BUCKETS_MS,
            registry=self.registry,
        )
        self.verifier_errors_total = Counter(
            "trustlens_verifier_errors_total",
            "Unrecoverable verification errors",
            ["kind"],
            registry=self.registry,
        )

        self.oracle_requests_total = Counter(
            "trustlens_oracle_requests_total",
            "Oracle lookups",
            ["oracle", "status"],
            registry=self.registry,
        )
        self.oracle_latency_ms = Histogram(
            "trustlens_oracle_latency_ms",
            "Oracle latency (ms)",
            ["oracle"],
            buckets=_LATENCY_BUCKETS_MS,
            registry=self.registry,
        )

        self.gateway_requests_total = Counter(
            "trustlens_gateway_requests_total",
            "Gateway requests",
            ["tenant", "backend", "status"],
            registry=self.registry,
        )
        self.gateway_latency_ms = Histogram(
            "trustlens_gateway_latency_ms",
            "Gateway end-to-end latency (ms)",
            ["tenant"],
            buckets=_LATENCY_BUCKETS_MS,
            registry=self.registry,
        )
        self.gateway_budget_rejects_total = Counter(
            "trustlens_gateway_budget_rejects_total",
            "Requests rejected for budget/rate reasons",
            ["tenant", "kind"],
            registry=self.registry,
        )

        self.circuit_state = Gauge(
            "trustlens_circuit_state",
            "Circuit breaker state (0=closed, 1=half_open, 2=open)",
            ["name"],
            registry=self.registry,
        )

        self.certificate_store_errors_total = Counter(
            "trustlens_certificate_store_errors_total",
            "Failures persisting certificates",
            registry=self.registry,
        )

        self.shadow_samples_total = Counter(
            "trustlens_shadow_samples_total",
            "Requests mirrored to shadow eval",
            ["tenant"],
            registry=self.registry,
        )

        self.deep_inspector_alarms_total = Counter(
            "trustlens_deep_inspector_alarms_total",
            "Deep Inspector SSH alarms fired",
            ["tenant", "severity"],
            registry=self.registry,
        )
        self.deep_inspector_steering_engage_total = Counter(
            "trustlens_deep_inspector_steering_engage_total",
            "RAD-CoT engagements",
            ["tenant"],
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)


class NullMetrics:
    """No-op metrics used when Prometheus is unwanted."""

    class _Label:
        def inc(self, *_a, **_kw): pass
        def observe(self, *_a, **_kw): pass
        def set(self, *_a, **_kw): pass

    class _NullMetric:
        def __init__(self): self._label = NullMetrics._Label()
        def labels(self, *_, **__): return self._label
        def inc(self, *_a, **_kw): pass
        def observe(self, *_a, **_kw): pass
        def set(self, *_a, **_kw): pass

    def __getattr__(self, _name):
        return NullMetrics._NullMetric()

    def render(self) -> bytes:
        return b""
