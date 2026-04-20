"""Ed25519 signing and offline verification of trust certificates.

The signer produces certificates whose signature covers the canonical-JSON
serialization of the payload. Verification is intentionally self-contained
so auditors can check certificates without any TrustLens service running.

Canonical JSON: sorted keys, no whitespace, UTF-8. This is reproducible
across language implementations.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

from trustlens.certificate.schema import Certificate, CertificatePayload
from trustlens.version import CERT_SCHEMA_VERSION, PIPELINE_VERSION


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

@dataclass
class KeyPair:
    """An Ed25519 keypair with a stable fingerprint key-id."""
    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey
    key_id: str

    @classmethod
    def generate(cls) -> "KeyPair":
        priv = Ed25519PrivateKey.generate()
        pub = priv.public_key()
        return cls(
            private_key=priv,
            public_key=pub,
            key_id=_public_key_fingerprint(pub),
        )

    @classmethod
    def from_private_pem(cls, pem: bytes, password: Optional[bytes] = None) -> "KeyPair":
        priv = serialization.load_pem_private_key(pem, password=password)
        if not isinstance(priv, Ed25519PrivateKey):
            raise ValueError("Expected Ed25519 private key")
        pub = priv.public_key()
        return cls(priv, pub, _public_key_fingerprint(pub))

    def private_pem(self, password: Optional[bytes] = None) -> bytes:
        enc = (
            serialization.BestAvailableEncryption(password)
            if password
            else serialization.NoEncryption()
        )
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=enc,
        )

    def public_pem(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )


def load_public_key_pem(pem: bytes) -> Ed25519PublicKey:
    pub = serialization.load_pem_public_key(pem)
    if not isinstance(pub, Ed25519PublicKey):
        raise ValueError("Expected Ed25519 public key")
    return pub


def _public_key_fingerprint(pub: Ed25519PublicKey) -> str:
    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"ed25519-{digest}"


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------

def canonical_json(obj) -> bytes:
    """Deterministic JSON encoding for signing.

    Uses sorted keys, ASCII-safe escaping, no whitespace. Crucially stable
    across Python, Go, Rust, JS implementations.
    """
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def payload_digest(payload: CertificatePayload) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def sign_certificate(payload: CertificatePayload, keypair: KeyPair) -> Certificate:
    """Sign a certificate payload. Idempotent for equal payloads."""
    if payload.schema_version != CERT_SCHEMA_VERSION:
        raise ValueError(
            f"Cannot sign certificate with schema_version={payload.schema_version}; "
            f"this signer emits {CERT_SCHEMA_VERSION}"
        )

    cert_id = payload_digest(payload)
    signature = keypair.private_key.sign(cert_id.encode("utf-8"))
    return Certificate(
        cert_id=cert_id,
        payload=payload,
        signature=base64.b64encode(signature).decode("ascii"),
        signer_key_id=keypair.key_id,
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass
class VerifyResult:
    valid: bool
    reason: str = "ok"
    pipeline_version_match: bool = True
    schema_version_match: bool = True

    def __bool__(self) -> bool:
        return self.valid


def verify_certificate(
    cert: Certificate,
    public_key: Ed25519PublicKey,
    *,
    require_pipeline_version: Optional[str] = PIPELINE_VERSION,
    require_schema_version: Optional[str] = CERT_SCHEMA_VERSION,
    trusted_key_ids: Optional[set[str]] = None,
) -> VerifyResult:
    """Offline-verify a certificate.

    Checks (in order):
        1. key_id matches the trusted allowlist (if provided)
        2. cert_id recomputes correctly from the payload
        3. Ed25519 signature is valid for cert_id under public_key
        4. schema_version matches (soft: returned as flag)
        5. pipeline_version matches (soft: returned as flag)
    """
    if trusted_key_ids is not None and cert.signer_key_id not in trusted_key_ids:
        return VerifyResult(
            valid=False, reason=f"signer_key_id {cert.signer_key_id} not trusted"
        )

    expected = payload_digest(cert.payload)
    if expected != cert.cert_id:
        return VerifyResult(valid=False, reason="cert_id does not match payload digest")

    try:
        sig = base64.b64decode(cert.signature.encode("ascii"))
        public_key.verify(sig, cert.cert_id.encode("utf-8"))
    except (InvalidSignature, ValueError) as e:
        return VerifyResult(valid=False, reason=f"signature invalid: {e}")

    schema_ok = (
        require_schema_version is None
        or cert.payload.schema_version == require_schema_version
    )
    pipeline_ok = (
        require_pipeline_version is None
        or cert.payload.pipeline_version == require_pipeline_version
    )

    return VerifyResult(
        valid=True,
        reason="ok",
        schema_version_match=schema_ok,
        pipeline_version_match=pipeline_ok,
    )
