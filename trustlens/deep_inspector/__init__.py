"""Deep Inspector — top-tier package with SSH + RAD-CoT + agentic chain.

Available to tenants on the DEEP_INSPECTOR tier. Optional heavy dependencies
(torch, transformers, hallucination-escape research code) are lazy-imported
so the base TrustLens package has zero GPU footprint.

The package provides three adapters and a DeepVerifierEngine that extends the
base VerifierEngine with spectral stability diagnostics and chain-level
trust propagation.
"""

from __future__ import annotations

from trustlens.deep_inspector.ssh_adapter import (
    SSHAdapter,
    SSHConfig,
    SSHSnapshot,
    StubSSHAdapter,
)
from trustlens.deep_inspector.steering_adapter import (
    SteeringAdapter,
    SteeringConfig,
    SteeringEvent,
    StubSteeringAdapter,
)
from trustlens.deep_inspector.agentic_chain import (
    ChainEdge,
    ChainNode,
    TrustChain,
)
from trustlens.deep_inspector.engine import (
    DeepVerifierEngine,
    DeepVerificationRequest,
    DeepVerificationResult,
)

__all__ = [
    "SSHAdapter",
    "SSHConfig",
    "SSHSnapshot",
    "StubSSHAdapter",
    "SteeringAdapter",
    "SteeringConfig",
    "SteeringEvent",
    "StubSteeringAdapter",
    "ChainEdge",
    "ChainNode",
    "TrustChain",
    "DeepVerifierEngine",
    "DeepVerificationRequest",
    "DeepVerificationResult",
]
