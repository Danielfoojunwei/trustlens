"""End-to-end verifier engine tests using the in-memory KB oracle."""

from __future__ import annotations

import pytest

from trustlens.certificate.schema import CertificateStatus, ClaimVerdict
from trustlens.oracles.customer_kb import (
    CustomerKBOracle,
    KBDocument,
    LexicalKBIndex,
)
from trustlens.oracles.registry import OracleRegistry, OracleSelection
from trustlens.verifier.engine import VerificationRequest, VerifierEngine


pytestmark = pytest.mark.asyncio


@pytest.fixture()
def engine() -> VerifierEngine:
    index = LexicalKBIndex()
    index.add_many([
        KBDocument(
            doc_id="d1",
            text="Paris is the capital and most populous city of France.",
            source_uri="kb://france/paris",
        ),
        KBDocument(
            doc_id="d2",
            text="The boiling point of water at sea level is 100 degrees Celsius.",
            source_uri="kb://water",
        ),
    ], tenant_id="t1")
    registry = OracleRegistry([CustomerKBOracle(index)])
    return VerifierEngine(registry)


async def test_verified_claim_emits_verified_status(engine: VerifierEngine) -> None:
    req = VerificationRequest(
        prompt="What is the capital of France?",
        response_text="Paris is the capital of France.",
        tenant_id="t1",
        request_id="r1",
        model_id="echo",
        oracle_selection=OracleSelection(priority_order=["customer_kb"], deadline_ms=500),
        tau=0.3,           # be tolerant for the lexical baseline
        tau_prime=0.05,
    )
    result = await engine.verify(req)
    assert result.payload.overall_status in (
        CertificateStatus.VERIFIED, CertificateStatus.PARTIAL
    )
    assert any(c.verdict == ClaimVerdict.VERIFIED for c in result.payload.claims)


async def test_unsupported_claim_is_masked(engine: VerifierEngine) -> None:
    req = VerificationRequest(
        prompt="What is the capital of Atlantis?",
        response_text="Atlantis is the capital of the Sublunary Empire.",
        tenant_id="t1",
        request_id="r2",
        model_id="echo",
        oracle_selection=OracleSelection(priority_order=["customer_kb"], deadline_ms=500),
        tau=0.6,
        tau_prime=0.3,
    )
    result = await engine.verify(req)
    assert result.payload.overall_status == CertificateStatus.BLOCKED
    assert result.masked_claim_ids
    assert all(not c.is_renderable for c in result.payload.claims)
