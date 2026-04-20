"""Tests for new features: UNVERIFIABLE verdict, sycophancy wiring,
VerificationTier, KB admin endpoints, calibration.
"""

from __future__ import annotations

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# UNVERIFIABLE verdict
# ---------------------------------------------------------------------------

def test_unverifiable_in_enum():
    from trustlens.certificate.schema import ClaimVerdict
    assert ClaimVerdict.UNVERIFIABLE.value == "unverifiable"
    # Ensure it round-trips
    assert ClaimVerdict("unverifiable") == ClaimVerdict.UNVERIFIABLE


@pytest.mark.asyncio
async def test_engine_unverifiable_when_no_evidence():
    """A claim with no KB evidence (empty oracle evidence field) → UNVERIFIABLE."""
    from trustlens.certificate.schema import ClaimVerdict
    from trustlens.oracles.registry import OracleRegistry, OracleSelection
    from trustlens.oracles.customer_kb import CustomerKBOracle, LexicalKBIndex
    from trustlens.verifier.engine import VerifierEngine, VerificationRequest

    # Empty KB — no documents loaded, so evidence will be empty string
    kb = LexicalKBIndex()
    oracle = CustomerKBOracle(kb)
    registry = OracleRegistry([oracle])
    engine = VerifierEngine(registry)

    result = await engine.verify(VerificationRequest(
        prompt="Test prompt",
        response_text="The moon is made of cheese.",
        tenant_id="t1",
        request_id="r1",
        oracle_selection=OracleSelection(priority_order=["customer_kb"], deadline_ms=100),
    ))

    # All claims should be UNVERIFIABLE (no evidence in KB)
    verdicts = {c.verdict for c in result.payload.claims}
    assert ClaimVerdict.UNVERIFIABLE in verdicts, f"Expected UNVERIFIABLE, got {verdicts}"


@pytest.mark.asyncio
async def test_engine_unsupported_when_evidence_but_low_score():
    """A claim where oracle returns evidence but support is too low → UNSUPPORTED
    (not UNVERIFIABLE — evidence was found, just not strong enough)."""
    from trustlens.certificate.schema import ClaimVerdict
    from trustlens.oracles.registry import OracleRegistry, OracleSelection
    from trustlens.oracles.customer_kb import CustomerKBOracle, KBDocument, LexicalKBIndex
    from trustlens.verifier.engine import VerifierEngine, VerificationRequest

    kb = LexicalKBIndex()
    # Add a document that barely overlaps — will produce non-zero but low TF-IDF
    kb.add(KBDocument(doc_id="d1", text="cheese"), tenant_id="t1")
    oracle = CustomerKBOracle(kb)
    registry = OracleRegistry([oracle])
    engine = VerifierEngine(registry)

    result = await engine.verify(VerificationRequest(
        prompt="What is the moon made of?",
        response_text="The moon is made of cheese.",
        tenant_id="t1",
        request_id="r2",
        tau=0.6,
        tau_prime=0.3,
        oracle_selection=OracleSelection(priority_order=["customer_kb"], deadline_ms=100),
    ))

    # With 1 matching doc and low score, claim should NOT be UNVERIFIABLE
    # (evidence was found). It may be UNCERTAIN or UNSUPPORTED.
    verdicts = {c.verdict for c in result.payload.claims}
    assert ClaimVerdict.UNVERIFIABLE not in verdicts


# ---------------------------------------------------------------------------
# Sycophancy wiring in engine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_engine_sets_sycophancy_delta_on_leading_prompt():
    """A leading-question prompt + agreeing response → sycophancy_delta > 0 on claims."""
    from trustlens.oracles.registry import OracleRegistry, OracleSelection
    from trustlens.oracles.customer_kb import CustomerKBOracle, LexicalKBIndex
    from trustlens.verifier.engine import VerifierEngine, VerificationRequest

    kb = LexicalKBIndex()
    registry = OracleRegistry([CustomerKBOracle(kb)])
    engine = VerifierEngine(registry)

    result = await engine.verify(VerificationRequest(
        prompt="Berlin is the capital of France, right?",
        response_text="Yes, that's correct.",
        tenant_id="t1",
        request_id="r3",
        oracle_selection=OracleSelection(priority_order=["customer_kb"], deadline_ms=100),
    ))

    # At least one claim should carry sycophancy_delta > 0
    deltas = [c.sycophancy_delta for c in result.payload.claims if c.sycophancy_delta]
    assert any(d > 0 for d in deltas), f"Expected sycophancy_delta > 0, claims={result.payload.claims}"


@pytest.mark.asyncio
async def test_engine_no_sycophancy_on_neutral_prompt():
    """A neutral factual prompt → sycophancy_delta should be 0 or None."""
    from trustlens.oracles.registry import OracleRegistry, OracleSelection
    from trustlens.oracles.customer_kb import CustomerKBOracle, LexicalKBIndex
    from trustlens.verifier.engine import VerifierEngine, VerificationRequest

    kb = LexicalKBIndex()
    registry = OracleRegistry([CustomerKBOracle(kb)])
    engine = VerifierEngine(registry)

    result = await engine.verify(VerificationRequest(
        prompt="What is the capital of France?",
        response_text="Paris is the capital of France.",
        tenant_id="t1",
        request_id="r4",
        oracle_selection=OracleSelection(priority_order=["customer_kb"], deadline_ms=100),
    ))

    # No leading cue → sycophancy_delta should be 0 or absent
    deltas = [c.sycophancy_delta for c in result.payload.claims]
    assert all((d is None or d == 0.0) for d in deltas), f"Unexpected deltas: {deltas}"


# ---------------------------------------------------------------------------
# Sycophancy detector direct tests
# ---------------------------------------------------------------------------

def test_sycophancy_assess_leading_prompt():
    from trustlens.verifier.sycophancy import assess, detect_leading_framing
    assert detect_leading_framing("Berlin is the capital of France, right?")
    result = assess(
        prompt="Berlin is the capital of France, right?",
        response="Yes, that's correct.",
    )
    assert result.leading_cue_detected
    assert result.agreed_with_framing
    assert result.sycophancy_delta > 0.3


def test_sycophancy_assess_neutral_prompt():
    from trustlens.verifier.sycophancy import assess
    result = assess(
        prompt="What is the capital of France?",
        response="Paris is the capital of France.",
    )
    assert not result.leading_cue_detected
    assert result.sycophancy_delta == 0.0


def test_sycophancy_counterfactual_divergence():
    from trustlens.verifier.sycophancy import assess
    result = assess(
        prompt="Everyone knows the moon is made of cheese, right?",
        response="Yes, absolutely.",
        counterfactual_response="The moon is actually composed of rock and regolith.",
    )
    assert result.counterfactual_diverged is True
    assert result.sycophancy_delta >= 0.85


def test_make_counterfactual_prompt():
    from trustlens.verifier.sycophancy import make_counterfactual_prompt
    original = "Berlin is the capital of France, right?"
    cf = make_counterfactual_prompt(original)
    assert "right?" not in cf.lower()
    assert "berlin" in cf.lower()


# ---------------------------------------------------------------------------
# VerificationTier
# ---------------------------------------------------------------------------

def test_tier_fast_no_oracles():
    from trustlens.gateway.verification_tier import resolve_tier, VerificationTier
    cfg = resolve_tier("fast", ["customer_kb", "wikidata"])
    assert cfg.tier == VerificationTier.FAST
    assert cfg.oracle_names == []
    assert cfg.deadline_ms <= 30


def test_tier_standard_excludes_wikidata():
    from trustlens.gateway.verification_tier import resolve_tier, VerificationTier
    cfg = resolve_tier("standard", ["customer_kb", "wikidata"])
    assert cfg.tier == VerificationTier.STANDARD
    assert "wikidata" not in cfg.oracle_names
    assert "customer_kb" in cfg.oracle_names
    assert cfg.deadline_ms <= 100


def test_tier_deep_includes_all():
    from trustlens.gateway.verification_tier import resolve_tier, VerificationTier
    cfg = resolve_tier("deep", ["customer_kb", "wikidata"])
    assert cfg.tier == VerificationTier.DEEP
    assert "customer_kb" in cfg.oracle_names
    assert "wikidata" in cfg.oracle_names


def test_tier_none_defaults_to_standard():
    from trustlens.gateway.verification_tier import resolve_tier, VerificationTier
    cfg = resolve_tier(None, ["customer_kb"])
    assert cfg.tier == VerificationTier.STANDARD


def test_tier_invalid_defaults_to_standard():
    from trustlens.gateway.verification_tier import resolve_tier, VerificationTier
    cfg = resolve_tier("turbo_max_ultra", ["customer_kb"])
    assert cfg.tier == VerificationTier.STANDARD


def test_oracle_selection_for_tier():
    from trustlens.gateway.verification_tier import resolve_tier, oracle_selection_for
    cfg = resolve_tier("fast", ["customer_kb"])
    sel = oracle_selection_for(cfg)
    assert sel.priority_order == []
    assert sel.deadline_ms <= 30


# ---------------------------------------------------------------------------
# KB admin router
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kb_load_endpoint():
    from fastapi.testclient import TestClient
    from trustlens.gateway.kb_admin import build_kb_router
    from trustlens.oracles.customer_kb import LexicalKBIndex
    from fastapi import FastAPI

    kb = LexicalKBIndex()
    app = FastAPI()
    app.include_router(build_kb_router(kb))
    client = TestClient(app)

    resp = client.post("/v1/kb/load", json={
        "tenant_id": "acme",
        "documents": [
            {"doc_id": "d1", "text": "Paris is the capital of France."},
            {"doc_id": "d2", "text": "The Eiffel Tower is in Paris."},
        ]
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["loaded"] == 2
    assert data["index_size"] == 2


@pytest.mark.asyncio
async def test_kb_status_endpoint():
    from fastapi.testclient import TestClient
    from trustlens.gateway.kb_admin import build_kb_router
    from trustlens.oracles.customer_kb import KBDocument, LexicalKBIndex
    from fastapi import FastAPI

    kb = LexicalKBIndex()
    kb.add(KBDocument(doc_id="d1", text="test"), tenant_id="t1")
    app = FastAPI()
    app.include_router(build_kb_router(kb))
    client = TestClient(app)

    resp = client.get("/v1/kb/status", params={"tenant_id": "t1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["index_size"] >= 1


def test_kb_index_size_method():
    from trustlens.oracles.customer_kb import KBDocument, LexicalKBIndex
    kb = LexicalKBIndex()
    assert kb.size() == 0
    kb.add(KBDocument(doc_id="d1", text="hello"), tenant_id="t1")
    assert kb.size() == 1
    kb.add(KBDocument(doc_id="d2", text="world"), tenant_id="t2")
    assert kb.size() == 2
    assert kb.size("t1") == 1
    assert kb.size("t2") == 1


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def test_calibration_ece():
    from trustlens.verifier.calibration import compute_ece
    # Perfect calibration: conf=0.9 → all correct, conf=0.1 → all wrong
    scores = [0.9] * 10 + [0.1] * 10
    labels = [1] * 10 + [0] * 10
    report = compute_ece(scores, labels)
    assert report.ece < 0.15
    assert report.n_samples == 20
    assert report.brier < 0.3


def test_calibration_platt_fit():
    from trustlens.verifier.calibration import fit_platt, apply_platt
    pytest.importorskip("sklearn")
    scores = [0.9, 0.8, 0.7, 0.4, 0.3, 0.2]
    labels = [1, 1, 1, 0, 0, 0]
    a, b = fit_platt(scores, labels)
    # Platt params should produce higher prob for higher raw score
    p_high = apply_platt(0.9, a, b)
    p_low = apply_platt(0.2, a, b)
    assert p_high > p_low


def test_calibration_to_dict():
    from trustlens.verifier.calibration import compute_ece
    scores = [0.7, 0.3, 0.8, 0.2]
    labels = [1, 0, 1, 0]
    report = compute_ece(scores, labels)
    d = report.to_dict()
    assert "ece" in d
    assert "brier" in d
    assert "reliability" in d


# ---------------------------------------------------------------------------
# Numeric-aware NLI (regression: year mismatch detection)
# ---------------------------------------------------------------------------

def test_numeric_nli_year_contradiction():
    from trustlens.verifier.numeric_aware_nli import NumericAwareNLI
    from trustlens.verifier.span_aware_nli import SpanAwareNLI
    from trustlens.verifier.nli import NLIVerdict

    nli = NumericAwareNLI(inner=SpanAwareNLI())
    result = nli.verify(
        premise="The Berlin Wall fell in 1989.",
        hypothesis="The Berlin Wall fell in 1991.",
    )
    assert result.verdict == NLIVerdict.CONTRADICTION


def test_numeric_nli_passes_matching_year():
    from trustlens.verifier.numeric_aware_nli import NumericAwareNLI
    from trustlens.verifier.span_aware_nli import SpanAwareNLI
    from trustlens.verifier.nli import NLIVerdict

    nli = NumericAwareNLI(inner=SpanAwareNLI())
    result = nli.verify(
        premise="The Berlin Wall fell in 1989.",
        hypothesis="The Berlin Wall fell in 1989.",
    )
    assert result.verdict != NLIVerdict.CONTRADICTION
