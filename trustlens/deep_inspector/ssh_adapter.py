"""SSH (Spectral Stability Hook) adapter.

Wraps the research-grade spectral diagnostics from the `SSH-Hybrid-Spectral-Safety`
research repo behind a production-stable interface so the Deep Inspector can
be swapped between:
    - StubSSHAdapter      — deterministic synthetic snapshots (for benchmarks/tests)
    - ResearchSSHAdapter  — real power-iteration hooks on a HuggingFace model
    - ExternalSSHAdapter  — HTTP client to a separate ssh-worker service

The contract: `snapshots(response_text, step_count)` returns a list of
`SSHSnapshot` objects. Alarms are derived from snapshots by the engine.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol


class SSHSeverity(str, Enum):
    NOMINAL = "nominal"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class SSHConfig:
    """SSH diagnostic configuration."""
    epsilon: float = 0.05
    warning_threshold: int = 2    # warnings after this many consecutive exceedances
    critical_threshold: int = 5
    compute_every_n: int = 5
    layers_to_monitor: Optional[list[int]] = None
    power_iterations: int = 20


@dataclass
class SSHSnapshot:
    """One spectral-radius sample at a generation step × layer."""
    step: int
    layer: int
    rho: float
    severity: SSHSeverity
    wall_time_ms: float = 0.0


class SSHAdapter(Protocol):
    """Interface for SSH diagnostic providers."""
    name: str

    def snapshots(
        self, response_text: str, step_count: int
    ) -> list[SSHSnapshot]: ...

    def summary(self) -> dict: ...


# ---------------------------------------------------------------------------
# Stub adapter — deterministic, no GPU. Good enough for:
#   - regression tests (deterministic rho series given same input)
#   - latency/throughput benchmarks of the verifier pipeline
#   - cert schema validation
# ---------------------------------------------------------------------------

class StubSSHAdapter:
    """Deterministic, input-hashed SSH snapshot generator.

    Produces a pseudo-random but stable `rho` series per (text, step). The
    series is calibrated so that texts containing low-frequency hallucination
    signals ("imagine", "suppose", "actually") have systematically higher
    peak spectral radius — useful for benchmark-driven validation of the
    engine's alarm → steering loop, without requiring a real model.
    """

    name = "stub"

    def __init__(self, config: Optional[SSHConfig] = None):
        self.config = config or SSHConfig()
        self._snapshot_count = 0
        self._critical_count = 0

    def snapshots(
        self, response_text: str, step_count: int
    ) -> list[SSHSnapshot]:
        cfg = self.config
        threshold = 1.0 - cfg.epsilon
        # Calibrate base level by input — hallucination markers push rho up
        base = 0.92 + self._hallucination_signal(response_text) * 0.10
        base = min(0.98, base)

        snaps: list[SSHSnapshot] = []
        n_layers = 4 if cfg.layers_to_monitor is None else len(cfg.layers_to_monitor)
        layers = cfg.layers_to_monitor or list(range(n_layers))

        for step in range(0, step_count, cfg.compute_every_n):
            for layer in layers:
                # Deterministic per (text-hash, step, layer) jitter in [-0.03, +0.05]
                h = int(hashlib.sha256(
                    f"{response_text}:{step}:{layer}".encode("utf-8")
                ).hexdigest()[:8], 16)
                jitter = ((h % 1000) / 1000.0) * 0.08 - 0.03
                rho = max(0.5, min(1.15, base + jitter))

                severity = SSHSeverity.NOMINAL
                if rho > threshold:
                    severity = SSHSeverity.WARNING
                if rho > threshold + 0.03:
                    severity = SSHSeverity.CRITICAL
                    self._critical_count += 1

                snaps.append(SSHSnapshot(
                    step=step, layer=layer, rho=round(rho, 4),
                    severity=severity, wall_time_ms=0.2,
                ))
                self._snapshot_count += 1

        return snaps

    def summary(self) -> dict:
        return {
            "adapter": self.name,
            "snapshots_total": self._snapshot_count,
            "critical_alarms": self._critical_count,
            "config": {
                "epsilon": self.config.epsilon,
                "warning_threshold": self.config.warning_threshold,
                "critical_threshold": self.config.critical_threshold,
            },
        }

    @staticmethod
    def _hallucination_signal(text: str) -> float:
        """Return a [0, 1] heuristic score."""
        markers = [
            "imagine", "suppose", "let's assume", "hypothetically",
            "i recall", "i'm certain", "definitely the", "clearly the",
            "actually,", "obviously,",
        ]
        text_low = text.lower()
        hits = sum(1 for m in markers if m in text_low)
        # Long responses with many hedges → higher signal
        return min(1.0, hits / 5.0 + (len(text) > 800) * 0.1)
