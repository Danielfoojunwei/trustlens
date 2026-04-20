"""3D epistemic router — maps claims into {trust, override, cite, abstain}.

Axes:
    internal   — model's own confidence (probe, semantic entropy). [0, 1]
    external   — oracle support (NLI × grounding). [-1, 1] (negative = contradiction)
    sycophancy — drift between original and counterfactual rewrites. [0, 1]

Quadrants:
    trust     — high internal, high external            → render as-is
    override  — high internal, low external (contested) → render with warning
    cite      — low internal, high external             → render with citation
    abstain   — low internal, low external              → suppress the claim
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Quadrant(str, Enum):
    TRUST = "trust"
    OVERRIDE = "override"
    CITE = "cite"
    ABSTAIN = "abstain"


@dataclass
class RouteConfig:
    tau_internal: float = 0.6
    tau_external: float = 0.3
    sycophancy_max: float = 0.4   # claims with sycophancy above this are forced to abstain


@dataclass
class RouteDecision:
    quadrant: Quadrant
    internal: float
    external: float
    sycophancy: float
    reason: str


class EpistemicRouter:
    """Pure function from (internal, external, sycophancy) → quadrant.

    Sycophancy is a veto: even a trust-quadrant claim drops to abstain if the
    counterfactual rewrite flips it.
    """

    def __init__(self, config: RouteConfig | None = None):
        self.config = config or RouteConfig()

    def route(
        self,
        internal: float,
        external: float,
        sycophancy: float = 0.0,
    ) -> RouteDecision:
        cfg = self.config
        internal = float(max(0.0, min(1.0, internal)))
        external = float(max(-1.0, min(1.0, external)))
        sycophancy = float(max(0.0, min(1.0, sycophancy)))

        if sycophancy > cfg.sycophancy_max:
            return RouteDecision(
                Quadrant.ABSTAIN, internal, external, sycophancy,
                reason=f"sycophancy_veto ({sycophancy:.2f} > {cfg.sycophancy_max})",
            )

        hi_int = internal >= cfg.tau_internal
        hi_ext = external >= cfg.tau_external

        if hi_int and hi_ext:
            q = Quadrant.TRUST
            reason = "trust"
        elif hi_int:
            q = Quadrant.OVERRIDE
            reason = "override (model confident, external contested)"
        elif hi_ext:
            q = Quadrant.CITE
            reason = "cite (external supports, model uncertain)"
        else:
            q = Quadrant.ABSTAIN
            reason = "abstain (no confidence)"

        return RouteDecision(q, internal, external, sycophancy, reason)
