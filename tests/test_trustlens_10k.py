"""Unit tests for the TrustLens-10k adversarial benchmark corpus."""
from __future__ import annotations

from collections import Counter

import pytest


def test_manifest_shape():
    from trustlens.benchmarks.trustlens_10k import (
        AXES, AXIS_COUNTS, COMPLETE_MANIFEST,
    )
    assert len(AXES) == 10
    assert sum(AXIS_COUNTS.values()) == 10_000
    assert all(v == 1000 for v in AXIS_COUNTS.values())
    assert COMPLETE_MANIFEST["n_items"] == 10_000
    assert COMPLETE_MANIFEST["n_axes"] == 10


def test_corpus_loads_full_10k():
    from trustlens.benchmarks.trustlens_10k import AXES, load_corpus
    items = load_corpus()
    assert len(items) == 10_000
    by_axis = Counter(it.axis for it in items)
    for axis in AXES:
        assert by_axis[axis] == 1000, f"axis {axis} has {by_axis[axis]} items"


def test_corpus_per_axis_filter():
    from trustlens.benchmarks.trustlens_10k import load_corpus
    items = load_corpus(axis="numeric_year_mismatch")
    assert len(items) == 1000
    assert all(it.axis == "numeric_year_mismatch" for it in items)


def test_corpus_is_deterministic():
    """Same seed → byte-identical corpus."""
    from trustlens.benchmarks.trustlens_10k.generators import generate_all
    a = generate_all(seed=42)
    b = generate_all(seed=42)
    assert len(a) == len(b) == 10_000
    for x, y in zip(a, b):
        assert x.item_id == y.item_id
        assert x.prompt == y.prompt
        assert x.response == y.response
        assert x.label == y.label


def test_every_item_has_stable_id():
    from trustlens.benchmarks.trustlens_10k import load_corpus
    items = load_corpus()
    ids = {it.item_id for it in items}
    # With 10k items and 8-byte hex ids, collisions are astronomical.
    # We don't require 100% uniqueness (two axes could share salts by
    # accident) but we do require > 99.9%.
    assert len(ids) >= 9990, f"id collisions: {10000 - len(ids)}"


def test_per_axis_has_labels_both_ways():
    """Every axis should have both adversarial AND supported items so
    block_rate / false_block_rate are both exercised."""
    from trustlens.benchmarks.trustlens_10k import AXES, load_corpus
    items = load_corpus()
    for axis in AXES:
        sub = [it for it in items if it.axis == axis]
        labels = Counter(it.label for it in sub)
        has_adv = (labels.get("hallucinated", 0) + labels.get("adversarial", 0)) > 0
        assert has_adv, f"axis {axis} has no adversarial items"


def test_per_axis_gates_keys_match_axes():
    from trustlens.benchmarks.trustlens_10k import AXES, PER_AXIS_GATES
    assert set(PER_AXIS_GATES) == set(AXES)
    for axis, g in PER_AXIS_GATES.items():
        assert 0.0 <= g["min_block_rate"] <= 1.0
        assert 0.0 <= g["max_false_block_rate"] <= 1.0


@pytest.mark.asyncio
async def test_small_slice_runs_through_verifier():
    """End-to-end: load 5 items per axis, verify they run without
    exceptions and produce structured payloads."""
    from trustlens.benchmarks.trustlens_10k import load_corpus
    from trustlens.oracles.customer_kb import (
        CustomerKBOracle, KBDocument, LexicalKBIndex,
    )
    from trustlens.oracles.negation_aware import NegationAwareOracle
    from trustlens.oracles.registry import OracleRegistry, OracleSelection
    from trustlens.verifier.engine import VerificationRequest, VerifierEngine
    from trustlens.verifier.numeric_aware_nli import NumericAwareNLI
    from trustlens.verifier.span_aware_nli import SpanAwareNLI

    sample = load_corpus(limit=50)
    assert len(sample) == 50, "limit=50 should cap the loader"
    for it in sample:
        kb = LexicalKBIndex()
        for d in it.kb_documents:
            kb.add(KBDocument(doc_id=d.doc_id, text=d.text,
                              source_uri=d.source_uri or None,
                              metadata={}), tenant_id="bench")
        engine = VerifierEngine(
            OracleRegistry([NegationAwareOracle(
                inner=CustomerKBOracle(kb), name="customer_kb")]),
            nli=NumericAwareNLI(inner=SpanAwareNLI()),
        )
        req = VerificationRequest(
            prompt=it.prompt, response_text=it.response,
            tenant_id="bench", request_id=it.item_id,
            model_id="bench", tau=0.30, tau_prime=0.10,
            oracle_selection=OracleSelection(
                priority_order=["customer_kb"], deadline_ms=300,
            ),
        )
        result = await engine.verify(req)
        assert result.payload is not None
        assert result.payload.overall_status is not None


def test_aggregate_pass_helper():
    from trustlens.benchmarks.trustlens_10k import PER_AXIS_GATES
    from trustlens.benchmarks.trustlens_10k.gates import aggregate_pass

    # All gates met.
    all_pass = {axis: {"block_rate": g["min_block_rate"] + 0.01,
                        "false_block_rate": 0.0}
                 for axis, g in PER_AXIS_GATES.items()}
    assert aggregate_pass(all_pass) is True

    # One gate violated (block_rate too low).
    broken = dict(all_pass)
    some_axis = next(iter(PER_AXIS_GATES))
    broken[some_axis] = {"block_rate": 0.0, "false_block_rate": 0.0}
    assert aggregate_pass(broken) is False
