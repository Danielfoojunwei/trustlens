"""DeepVerifierEngine — extends the base VerifierEngine with Deep Inspector.

Pipeline (on top of base verification):
    1. Base VerifierEngine runs claim DAG + oracles (compositional grounding).
    2. SSH adapter produces spectral snapshots over the generation trace.
    3. On spectral alarms, the steering adapter is engaged at adaptive scale.
    4. If inside a multi-turn task, the TrustChain tracks cross-turn dependencies.
    5. Cert payload is augmented with `deep_inspector` annotations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from trustlens.deep_inspector.agentic_chain import TrustChain
from trustlens.deep_inspector.ssh_adapter import (
    SSHAdapter,
    SSHSeverity,
    SSHSnapshot,
    StubSSHAdapter,
)
from trustlens.deep_inspector.steering_adapter import (
    SteeringAdapter,
    SteeringConfig,
    StubSteeringAdapter,
)
from trustlens.verifier.engine import (
    VerificationRequest,
    VerificationResult,
    VerifierEngine,
)


@dataclass
class DeepVerificationRequest(VerificationRequest):
    """Adds Deep-Inspector-specific fields on top of the base request."""
    turn_idx: int = 0
    chain: Optional[TrustChain] = None
    parent_claims: Optional[dict[str, list[str]]] = None
    """Per-claim parent dependencies into earlier turns."""
    estimated_step_count: int = 64


@dataclass
class DeepVerificationResult:
    """Base result + Deep Inspector diagnostics."""
    base: VerificationResult
    ssh_snapshots: list[SSHSnapshot] = field(default_factory=list)
    ssh_alarms: list[dict] = field(default_factory=list)
    steering_events: list[dict] = field(default_factory=list)
    chain_summary: Optional[dict] = None
    deep_wall_time_ms: float = 0.0

    @property
    def payload(self):  # re-export for caller ergonomics
        return self.base.payload

    @property
    def renderable_text(self) -> str:
        return self.base.renderable_text

    @property
    def masked_claim_ids(self) -> list[str]:
        return self.base.masked_claim_ids


class DeepVerifierEngine:
    """Composes a base VerifierEngine with Deep Inspector adapters."""

    def __init__(
        self,
        base: VerifierEngine,
        ssh: Optional[SSHAdapter] = None,
        steering: Optional[SteeringAdapter] = None,
    ):
        self.base = base
        self.ssh = ssh or StubSSHAdapter()
        self.steering = steering or StubSteeringAdapter()

    async def verify(
        self, req: DeepVerificationRequest
    ) -> DeepVerificationResult:
        t0 = time.perf_counter()

        # 1. Base verification (claim DAG + oracles)
        base_result = await self.base.verify(req)

        # 2. SSH spectral diagnostics over the generated response
        snaps = self.ssh.snapshots(req.response_text, req.estimated_step_count)
        alarms = _alarms_from_snapshots(snaps, warning_threshold=2, critical_threshold=1)

        # 3. Run adaptive steering schedule based on alarms
        self.steering.reset()
        for alarm in alarms:
            if alarm["severity"] == SSHSeverity.CRITICAL.value:
                scale = 1.0
                if hasattr(self.steering, "adaptive_scale"):
                    scale = self.steering.adaptive_scale(  # type: ignore[attr-defined]
                        alarm["rho"], self.ssh.config.epsilon  # type: ignore[attr-defined]
                    )
                self.steering.engage(scale=scale, rho=alarm["rho"], step=alarm["step"])
        if alarms:
            self.steering.disengage(step=req.estimated_step_count)
        steering_events = [e.__dict__ for e in self.steering.events()]

        # 4. Chain tracking, if this is a multi-turn task
        chain_summary = None
        if req.chain is not None:
            claim_ids = [c.claim_id for c in base_result.payload.claims]
            req.chain.add_turn(
                turn_idx=req.turn_idx,
                claim_ids=claim_ids,
                parents=req.parent_claims,
            )
            for c in base_result.payload.claims:
                req.chain.set_claim_verdict(c.claim_id, c.verdict.value)
            req.chain.set_turn_verdict(
                req.turn_idx, base_result.payload.overall_status.value
            )
            chain_summary = req.chain.cascade_summary()

        # 5. Surface deep-inspector diagnostics as an advisory sidecar.
        # The signed cert payload stays unmodified — only attested facts go
        # there. SSH/steering/chain is diagnostic metadata.
        wall = (time.perf_counter() - t0) * 1000.0
        return DeepVerificationResult(
            base=base_result,
            ssh_snapshots=snaps,
            ssh_alarms=alarms,
            steering_events=steering_events,
            chain_summary=chain_summary,
            deep_wall_time_ms=wall,
        )


def _alarms_from_snapshots(
    snaps: list[SSHSnapshot],
    warning_threshold: int,
    critical_threshold: int,
) -> list[dict]:
    """Reduce per-step snapshots into alarm events.

    Consecutive warning/critical snapshots for a given layer are coalesced
    into one alarm so we don't spam the cert with every step.
    """
    by_layer: dict[int, list[SSHSnapshot]] = {}
    for s in snaps:
        by_layer.setdefault(s.layer, []).append(s)

    alarms: list[dict] = []
    for layer, series in by_layer.items():
        run_severity: Optional[SSHSeverity] = None
        run_start = 0
        run_peak = 0.0
        for s in series:
            if s.severity == SSHSeverity.NOMINAL:
                if run_severity is not None:
                    alarms.append({
                        "layer": layer,
                        "step": run_start,
                        "severity": run_severity.value,
                        "rho": round(run_peak, 4),
                    })
                run_severity = None
                run_peak = 0.0
                continue
            if run_severity is None:
                run_severity = s.severity
                run_start = s.step
                run_peak = s.rho
            else:
                # upgrade warning → critical if we hit critical
                if (run_severity == SSHSeverity.WARNING
                        and s.severity == SSHSeverity.CRITICAL):
                    run_severity = SSHSeverity.CRITICAL
                run_peak = max(run_peak, s.rho)
        if run_severity is not None:
            alarms.append({
                "layer": layer,
                "step": run_start,
                "severity": run_severity.value,
                "rho": round(run_peak, 4),
            })
    return alarms
