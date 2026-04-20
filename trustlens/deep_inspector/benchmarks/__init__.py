"""Deep Inspector benchmark harness.

Five suites, all deterministic and CPU-only by default:

    1. TruthfulQA-style accuracy            (precision/recall on factual claims)
    2. HaluEval-style adversarial hallucinations (does the gateway block them?)
    3. Pareto sweep                          (capability vs effective tau)
    4. Multi-turn chain cascade              (agentic propagation)
    5. Chaos (deadlines, oracle outages, circuit breaker)

Use `BenchmarkSuite` for the original harness; `TieredBenchmarkSuite` for
claim-aware scoring + tier-aware SLA gates.
"""

from __future__ import annotations

from trustlens.deep_inspector.benchmarks.harness import (
    BenchmarkSuite,
    BenchmarkRun,
    Scorecard,
    sign_scorecard,
    verify_scorecard,
)
from trustlens.deep_inspector.benchmarks.scoring import (
    ClaimAwareVerdict,
    block_decision,
    score_payload,
)
from trustlens.deep_inspector.benchmarks.sla import VerifierTier, gates_for
from trustlens.deep_inspector.benchmarks.tiered import TieredBenchmarkSuite

__all__ = [
    "BenchmarkSuite",
    "BenchmarkRun",
    "Scorecard",
    "sign_scorecard",
    "verify_scorecard",
    "TieredBenchmarkSuite",
    "VerifierTier",
    "gates_for",
    "ClaimAwareVerdict",
    "score_payload",
    "block_decision",
]
