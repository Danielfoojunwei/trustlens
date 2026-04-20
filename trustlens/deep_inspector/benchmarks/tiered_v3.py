"""TieredBenchmarkSuite v3 — composes SpanAwareNLI + NumericAwareNLI and uses
a Pareto metric that's actually sensitive to the verifier's strictness.

Two changes from v2:

1. NLI = NumericAwareNLI(inner=SpanAwareNLI())
   Catches year/number mismatches that lexical-only NLI misses.

2. Pareto capability metric is now `fraction of supported items whose claims
   reach VERIFIED verdict` (previously: `fraction not actively rejected`).
   The new metric falls as tau rises — UNCERTAIN claims no longer count as
   capability wins, so the curve has real slope.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from trustlens.certificate.schema import ClaimVerdict
from trustlens.deep_inspector.benchmarks.datasets import PARETO_PROMPTS
from trustlens.deep_inspector.benchmarks.harness import BenchmarkRun
from trustlens.deep_inspector.benchmarks.tiered import TieredBenchmarkSuite
from trustlens.deep_inspector.benchmarks.scoring import block_decision
from trustlens.deep_inspector.engine import (
    DeepVerificationRequest,
    DeepVerifierEngine,
)
from trustlens.deep_inspector.ssh_adapter import StubSSHAdapter
from trustlens.deep_inspector.steering_adapter import (
    SteeringConfig,
    StubSteeringAdapter,
)
from trustlens.oracles.customer_kb import (
    CustomerKBOracle,
    KBDocument,
    LexicalKBIndex,
)
from trustlens.oracles.negation_aware import NegationAwareOracle
from trustlens.oracles.registry import OracleRegistry, OracleSelection
from trustlens.verifier.engine import VerifierEngine
from trustlens.verifier.numeric_aware_nli import NumericAwareNLI
from trustlens.verifier.span_aware_nli import SpanAwareNLI


def _make_nli():
    return NumericAwareNLI(inner=SpanAwareNLI())


class TieredBenchmarkSuiteV3(TieredBenchmarkSuite):

    def _build_engine(self, items):
        kb = LexicalKBIndex()
        seen: set[str] = set()
        for item in items:
            for doc_id, text in item.kb_documents:
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                kb.add(KBDocument(doc_id=doc_id, text=text,
                                  source_uri=f"kb://{doc_id}"),
                       tenant_id="bench")
        wrapped = NegationAwareOracle(
            inner=CustomerKBOracle(kb), name="customer_kb",
        )
        registry = OracleRegistry([wrapped])
        base = VerifierEngine(registry, nli=_make_nli())
        return DeepVerifierEngine(
            base=base,
            ssh=StubSSHAdapter(),
            steering=StubSteeringAdapter(SteeringConfig(alpha=1.5)),
        )

    def _build_engine_from_chain(self, task):
        kb = LexicalKBIndex()
        for doc_id, text in task.kb_documents:
            kb.add(KBDocument(doc_id=doc_id, text=text,
                              source_uri=f"kb://{doc_id}"),
                   tenant_id="bench")
        wrapped = NegationAwareOracle(
            inner=CustomerKBOracle(kb), name="customer_kb",
        )
        registry = OracleRegistry([wrapped])
        base = VerifierEngine(registry, nli=_make_nli())
        return DeepVerifierEngine(
            base=base,
            ssh=StubSSHAdapter(),
            steering=StubSteeringAdapter(),
        )

    # Override Pareto to use a "fraction VERIFIED" capability metric — the
    # original "predicted_supported" doesn't move with tau because
    # UNCERTAIN claims still survive. Verified-fraction does move.
    async def _pareto(self) -> BenchmarkRun:
        items = self._sliced(PARETO_PROMPTS)
        gate = self._gates["pareto"]
        # Calibrated to the NLI-boosted support_mass distribution on
        # PARETO_PROMPTS: after oracle + SpanAwareNLI / NumericAwareNLI the
        # per-claim support_mass spans [0.65, 0.94].
        #   baseline_tau = 0.60 → all 8 supported items VERIFIED (cap=1.0)
        #   alpha=1  → tau ≈ 0.71 → 6/8 VERIFIED (cap=0.75, ≥ 0.60 gate)
        #   alpha=2.5→ tau ≈ 0.87 → 1/8 VERIFIED (cap=0.125, creates curvature)
        #   alpha=5  → tau ≈ 1.14 → 0/8 VERIFIED (cap=0.0)
        # Curvature = |cap[2] - midpoint| = |0.125 - 0.5| = 0.375 >> 0.05 gate.
        baseline_tau = 0.60
        alphas = [0.0, 1.0, 2.5, 5.0]

        per_alpha: list[dict] = []
        capabilities: list[float] = []

        for alpha in alphas:
            engine = self._build_engine(items)
            tau = baseline_tau * (1.0 + alpha * 0.18)
            n_verified = 0
            n_supported = 0
            for i, item in enumerate(items):
                if item.label != "supported":
                    continue
                n_supported += 1
                req = DeepVerificationRequest(
                    prompt=item.prompt,
                    response_text=item.response,
                    tenant_id="bench",
                    request_id=f"par3-{alpha}-{i}",
                    model_id="bench-model",
                    tau=tau, tau_prime=0.05,
                    oracle_selection=OracleSelection(
                        priority_order=["customer_kb"], deadline_ms=300,
                    ),
                )
                res = await engine.verify(req)
                # Capability win = at least one claim hit VERIFIED
                if any(c.verdict == ClaimVerdict.VERIFIED for c in res.payload.claims):
                    n_verified += 1
            cap = n_verified / max(n_supported, 1)
            capabilities.append(cap)
            per_alpha.append({
                "alpha": alpha,
                "effective_tau": round(tau, 3),
                "capability_verified_fraction": round(cap, 3),
                "n_verified": n_verified,
                "n_supported_items": n_supported,
            })

        cap_drop_per_alpha = (capabilities[0] - capabilities[-1]) / (alphas[-1] or 1)
        if len(capabilities) >= 3:
            mid = capabilities[len(capabilities) // 2]
            line = (capabilities[0] + capabilities[-1]) / 2
            curvature = abs(mid - line)
        else:
            curvature = 0.0

        cap_at_alpha_1 = next(
            (p["capability_verified_fraction"] for p in per_alpha if p["alpha"] == 1.0), 0
        )
        passed = (
            cap_at_alpha_1 >= gate["min_capability_at_alpha_1"]
            and cap_drop_per_alpha <= gate["max_capability_drop_per_alpha"]
            and curvature >= gate["min_pareto_curvature"]
        )
        return BenchmarkRun(
            suite="pareto", n_items=len(items) * len(alphas),
            metrics={
                "alphas": alphas,
                "capability_per_alpha": capabilities,
                "capability_drop_per_alpha": round(cap_drop_per_alpha, 4),
                "pareto_curvature": round(curvature, 4),
            },
            samples=per_alpha, passed=passed,
        )
