"""Certificate sign/verify roundtrip tests."""

from __future__ import annotations

import pytest

from trustlens.certificate.schema import (
    CertificatePayload,
    CertificateStatus,
    ClaimVerdict,
    VerifiedClaim,
)
from trustlens.certificate.signer import (
    KeyPair,
    sign_certificate,
    verify_certificate,
    payload_digest,
    canonical_json,
)
from trustlens.version import CERT_SCHEMA_VERSION, PIPELINE_VERSION


def _payload() -> CertificatePayload:
    return CertificatePayload(
        schema_version=CERT_SCHEMA_VERSION,
        pipeline_version=PIPELINE_VERSION,
        issued_at="2026-04-17T00:00:00+00:00",
        tenant_id="t1",
        request_id="req-1",
        model_id="echo",
        input_hash="0" * 64,
        output_hash="1" * 64,
        claims=[
            VerifiedClaim(
                claim_id="c_abc",
                text="Paris is the capital of France.",
                verdict=ClaimVerdict.VERIFIED,
                support_mass=0.9,
            ),
        ],
        overall_status=CertificateStatus.VERIFIED,
        renderable_text_hash="2" * 64,
    )


def test_sign_and_verify_roundtrip() -> None:
    kp = KeyPair.generate()
    payload = _payload()
    cert = sign_certificate(payload, kp)
    assert cert.cert_id == payload_digest(payload)
    assert cert.signer_key_id == kp.key_id
    result = verify_certificate(cert, kp.public_key)
    assert result.valid
    assert result.pipeline_version_match
    assert result.schema_version_match


def test_tampered_payload_fails() -> None:
    kp = KeyPair.generate()
    cert = sign_certificate(_payload(), kp)
    # Mutate a non-signed-over field is not possible because signature covers
    # cert_id, which is a hash of the entire canonical payload. Mutating any
    # payload byte invalidates cert_id.
    cert.payload.claims[0].text = "Lyon is the capital of France."
    result = verify_certificate(cert, kp.public_key)
    assert not result.valid
    assert "cert_id" in result.reason or "signature" in result.reason


def test_pem_roundtrip() -> None:
    kp = KeyPair.generate()
    pem = kp.private_pem()
    kp2 = KeyPair.from_private_pem(pem)
    assert kp2.key_id == kp.key_id
    cert = sign_certificate(_payload(), kp)
    assert verify_certificate(cert, kp2.public_key).valid


def test_canonical_json_is_stable() -> None:
    payload = _payload()
    a = canonical_json(payload)
    b = canonical_json(payload)
    assert a == b
    assert b" " not in a or b'": "' not in a  # no whitespace


def test_trusted_key_id_allowlist() -> None:
    kp = KeyPair.generate()
    other = KeyPair.generate()
    cert = sign_certificate(_payload(), kp)
    bad = verify_certificate(
        cert, kp.public_key, trusted_key_ids={other.key_id}
    )
    assert not bad.valid
    good = verify_certificate(
        cert, kp.public_key, trusted_key_ids={kp.key_id}
    )
    assert good.valid
