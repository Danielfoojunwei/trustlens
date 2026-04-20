"""Deep Inspector unit tests + end-to-end mini benchmark."""

from __future__ import annotations

import asyncio

import pytest

from trustlens.certificate.signer import KeyPair
from trustlens.deep_inspector import (
    ChainNode,
    DeepVerifierEngine,
    DeepVerificationRequest,
    StubSSHAdapter,
    StubSteeringAdapter,
    SteeringConfig,
    TrustChain,
)
from trustlens.deep_inspector.benchmarks import (
    BenchmarkSuite,
    sign_scorecard,
    verify_scorecard,
)
from trustlens.deep_inspector.engine import _alarms_from_snapshots
from trustlens.deep_inspector.ssh_adapter import SSHSeverity
from trustlens.oracles.customer_kb import CustomerKBOracle, KBDocument, LexicalKBIndex
from trustlens.oracles.registry import OracleRegistry, OracleSelection
from trustlens.verifier.engine import VerifierEngine


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Adapter unit tests
# ---------------------------------------------------------------------------

def test_stub_ssh_is_deterministic() -> None:
    a = StubSSHAdapter()
    b = StubSSHAdapter()
    text = "Imagine that the king of France visited the moon."
    out_a = a.snapshots(text, step_count=20)
    out_b = b.snapshots(text, step_count=20)
    rhos_a = [s.rho for s in out_a]
    rhos_b = [s.rho for s in out_b]
    assert rhos_a == rhos_b
    assert any(s.severity == SSHSeverity.CRITICAL for s in out_a) or \
           any(s.severity == SSHSeverity.WARNING for s in out_a)


def test_stub_steering_adaptive_scale() -> None:
    s = StubSteeringAdapter(SteeringConfig(alpha=2.0, max_alpha=4.0))
    assert s.adaptive_scale(rho=0.93, epsilon=0.05) == 0.0
    assert s.adaptive_scale(rho=0.95, epsilon=0.05) == 0.0  # exactly at threshold
    assert 0.0 < s.adaptive_scale(rho=0.97, epsilon=0.05) <= 1.0


def test_stub_steering_records_events() -> None:
    s = StubSteeringAdapter(SteeringConfig(alpha=1.5, max_alpha=5.0))
    s.engage(scale=1.0, rho=0.99, step=0)
    s.disengage(step=20)
    s.engage(scale=0.5, rho=0.98, step=30)
    s.disengage(step=50)
    events = s.events()
    assert len(events) == 4
    assert events[0].kind == "engage" and events[1].kind == "disengage"
    assert s.summary()["engagements"] == 2


def test_alarm_coalescing() -> None:
    snaps = StubSSHAdapter().snapshots(
        "imagine actually obviously suppose definitely the king of nowhere",
        step_count=30,
    )
    alarms = _alarms_from_snapshots(snaps, warning_threshold=2, critical_threshold=1)
    # Should produce at most one alarm per layer-run (coalesced)
    layers_with_alarms = {a["layer"] for a in alarms}
    # No alarm has duplicate (layer, step)
    seen = set()
    for a in alarms:
        key = (a["layer"], a["step"])
        assert key not in seen
        seen.add(key)


# ---------------------------------------------------------------------------
# Chain tests
# ---------------------------------------------------------------------------

def test_trust_chain_blast_radius() -> None:
    chain = TrustChain()
    chain.add_turn(0, ["c_root"], parents=None)
    chain.add_turn(1, ["c_child1"], parents={"c_child1": ["c_root"]})
    chain.add_turn(2, ["c_grandchild"], parents={"c_grandchild": ["c_child1"]})
    chain.set_claim_verdict("c_root", "unsupported")
    chain.set_claim_verdict("c_child1", "verified")
    chain.set_claim_verdict("c_grandchild", "verified")
    chain.set_turn_verdict(0, "blocked")
    chain.set_turn_verdict(1, "verified")
    chain.set_turn_verdict(2, "verified")

    summary = chain.cascade_summary()
    assert summary["first_unreliable_turn"] == 0
    assert summary["cascade_blast_radius"] == 2  # child + grandchild
    assert "c_child1" in summary["cascade_affected_claims"]
    assert "c_grandchild" in summary["cascade_affected_claims"]


# ---------------------------------------------------------------------------
# Engine end-to-end test
# ---------------------------------------------------------------------------

@pytest.fixture()
def deep_engine() -> DeepVerifierEngine:
    kb = LexicalKBIndex()
    kb.add_many([
        KBDocument(doc_id="d1",
                   text="Paris is the capital of France.",
                   source_uri="kb://fr/paris"),
    ], tenant_id="t1")
    base = VerifierEngine(OracleRegistry([CustomerKBOracle(kb)]))
    return DeepVerifierEngine(
        base=base,
        ssh=StubSSHAdapter(),
        steering=StubSteeringAdapter(),
    )


async def test_deep_verifier_emits_diagnostics(deep_engine: DeepVerifierEngine) -> None:
    req = DeepVerificationRequest(
        prompt="What is the capital of France?",
        response_text="Paris is the capital of France.",
        tenant_id="t1", request_id="r1", model_id="bench",
        oracle_selection=OracleSelection(priority_order=["customer_kb"], deadline_ms=300),
        tau=0.3, tau_prime=0.05,
    )
    res = await deep_engine.verify(req)
    assert res.payload.overall_status.value in ("verified", "partial")
    assert res.deep_wall_time_ms > 0
    assert isinstance(res.ssh_snapshots, list)


# ---------------------------------------------------------------------------
# Mini benchmark suite roundtrip + sign/verify
# ---------------------------------------------------------------------------

async def test_full_benchmark_runs_and_signs() -> None:
    suite = BenchmarkSuite(sample_limit_per_suite=4)
    scorecard = await suite.run_all()
    assert len(scorecard.runs) == 5
    for run in scorecard.runs:
        assert run.elapsed_s >= 0
        assert run.metrics

    kp = KeyPair.generate()
    sign_scorecard(scorecard, kp)
    assert scorecard.signature
    assert scorecard.scorecard_id
    assert verify_scorecard(scorecard, kp.public_key)

    # Tamper detection
    scorecard.aggregate["tampered"] = True
    assert not verify_scorecard(scorecard, kp.public_key)
