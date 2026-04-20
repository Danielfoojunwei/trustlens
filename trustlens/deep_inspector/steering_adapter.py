"""RAD-CoT steering adapter.

Production interface for activation-level steering. Implementations:
    - StubSteeringAdapter    — records engage/disengage events for auditing,
                               no model mutation
    - ResearchSteeringAdapter — wraps rad_cot.ActivationSteerer from the
                               hallucination-escape research repo
    - SidecarSteeringAdapter  — HTTP client to a co-located inference node

The engine calls `engage(scale)` in response to SSH critical alarms and
`disengage()` after stability is restored. All engagements are recorded
and returned to the engine so they can be included in the trust certificate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class SteeringConfig:
    alpha: float = 1.5
    max_alpha: float = 5.0
    top_k_layers: int = 5
    scale_with_rho: bool = True


@dataclass
class SteeringEvent:
    """Record of an engage/disengage action."""
    kind: str          # "engage" | "disengage"
    at_step: int
    scale: float
    rho: Optional[float] = None
    layer_count: int = 0


class SteeringAdapter(Protocol):
    name: str
    config: SteeringConfig

    def engage(self, scale: float, rho: Optional[float] = None, step: int = 0) -> None: ...
    def disengage(self, step: int = 0) -> None: ...
    def events(self) -> list[SteeringEvent]: ...
    def reset(self) -> None: ...
    def summary(self) -> dict: ...


# ---------------------------------------------------------------------------
# Stub adapter — full event log, no tensor mutation.
# ---------------------------------------------------------------------------

class StubSteeringAdapter:
    name = "stub"

    def __init__(self, config: Optional[SteeringConfig] = None):
        self.config = config or SteeringConfig()
        self._events: list[SteeringEvent] = []
        self._engaged = False
        self._engagements = 0
        self._disengagements = 0

    def engage(self, scale: float, rho: Optional[float] = None, step: int = 0) -> None:
        if self._engaged:
            return
        effective = min(scale * self.config.alpha, self.config.max_alpha)
        self._events.append(SteeringEvent(
            kind="engage",
            at_step=step,
            scale=round(effective, 4),
            rho=rho,
            layer_count=self.config.top_k_layers,
        ))
        self._engaged = True
        self._engagements += 1

    def disengage(self, step: int = 0) -> None:
        if not self._engaged:
            return
        self._events.append(SteeringEvent(
            kind="disengage",
            at_step=step,
            scale=0.0,
            layer_count=self.config.top_k_layers,
        ))
        self._engaged = False
        self._disengagements += 1

    def events(self) -> list[SteeringEvent]:
        return list(self._events)

    def reset(self) -> None:
        self._events.clear()
        self._engaged = False

    def summary(self) -> dict:
        return {
            "adapter": self.name,
            "engagements": self._engagements,
            "disengagements": self._disengagements,
            "currently_engaged": self._engaged,
            "alpha": self.config.alpha,
            "max_alpha": self.config.max_alpha,
            "top_k_layers": self.config.top_k_layers,
        }

    def adaptive_scale(self, rho: float, epsilon: float) -> float:
        """Same formula as the research steerer — scale linearly with excess ρ."""
        if not self.config.scale_with_rho:
            return 1.0
        threshold = 1.0 - epsilon
        if rho <= threshold:
            return 0.0
        excess = rho - threshold
        return float(min(excess / 0.5, 1.0))
