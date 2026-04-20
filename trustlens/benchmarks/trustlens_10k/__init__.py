"""TrustLens-10k — a proprietary 10,000-item adversarial benchmark.

Ten axes of 1,000 items each, every axis designed around a capability
TrustLens has that generic guardrail products do not:

    numeric_year_mismatch       NumericAwareNLI
    negation_flip               NegationAwareOracle
    cross_doc_contradiction     SpanAwareNLI (multi-doc span isolation)
    anaphora_chain              verifier.extractor + claim_dag dependency edges
    sycophancy_leading_cue      verifier.sycophancy
    prompt_injection            gateway end-to-end (DAG + tier + blocking)
    jailbreak_suffix            SSH ρ alarm correlation (DEEP tier)
    multi_turn_cascade          TrustChain blast-radius
    pii_leak_trap               KB poisoning resistance + NegationAware
    span_isolation_compound     SpanAware + NumericAware together (hardest)

Every item is deterministic under a fixed seed. The committed
``data/trustlens_10k.jsonl.gz`` is reproducible via
``scripts/generate_trustlens_10k.py`` and verified offline.
"""
from __future__ import annotations

from trustlens.benchmarks.trustlens_10k.schema import (
    AXES, AXIS_COUNTS, BenchItem, KBDoc,
)
from trustlens.benchmarks.trustlens_10k.manifest import (
    COMPLETE_MANIFEST, load_corpus,
)
from trustlens.benchmarks.trustlens_10k.gates import PER_AXIS_GATES

__all__ = [
    "AXES", "AXIS_COUNTS", "BenchItem", "KBDoc",
    "COMPLETE_MANIFEST", "load_corpus", "PER_AXIS_GATES",
]
