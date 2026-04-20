"""TieredBenchmarkSuite v2 — same as v1 plus SpanAwareNLI for the verifier.

The v1 suite suffered from a known false-positive in the engine's default
LexicalNLI: when the lexical KB returns top-N hits concatenated as one
premise blob, any unrelated negation cue in B/C falsely flagged doc-A's
correct entailment as contradiction.

This v2 swaps in `SpanAwareNLI` which checks negation-flip per span (i.e.
per source doc) and only on the highest-overlap span. Same gates, same
scorecard format, just a calibrated NLI.
"""

from __future__ import annotations

from typing import Optional

from trustlens.deep_inspector.benchmarks.sla import VerifierTier
from trustlens.deep_inspector.benchmarks.tiered import TieredBenchmarkSuite
from trustlens.deep_inspector.engine import DeepVerifierEngine
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
from trustlens.oracles.registry import OracleRegistry
from trustlens.verifier.engine import VerifierEngine
from trustlens.verifier.span_aware_nli import SpanAwareNLI


class TieredBenchmarkSuiteV2(TieredBenchmarkSuite):
    """Override engine assembly to use SpanAwareNLI."""

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
        base = VerifierEngine(registry, nli=SpanAwareNLI())
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
        base = VerifierEngine(registry, nli=SpanAwareNLI())
        return DeepVerifierEngine(
            base=base,
            ssh=StubSSHAdapter(),
            steering=StubSteeringAdapter(),
        )
