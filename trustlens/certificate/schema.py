"""Trust certificate schema.

A Certificate is the product's audit artifact: a signed, content-addressed
JSON document that attests to what was verified and how. Enterprise buyers
show these to regulators.

Design principles
-----------------
1. Self-describing — `schema_version` and `pipeline_version` pin semantics.
2. Content-addressed — `cert_id` is deterministic hash of the signed payload.
3. Offline-verifiable — signature + oracle receipts can be validated without
   any TrustLens service running.
4. Append-only — revocations are separate objects, not mutations.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class ClaimVerdict(str, Enum):
    VERIFIED = "verified"          # supported by oracle(s), support_mass >= tau
    UNCERTAIN = "uncertain"        # tau_prime <= support_mass < tau
    UNSUPPORTED = "unsupported"    # support_mass < tau_prime, no direct contradiction
    UNVERIFIABLE = "unverifiable"  # no KB evidence found at all — absent ≠ false
    CONTRADICTED = "contradicted"  # oracle actively refutes
    DEPENDENCY_FAILED = "dependency_failed"  # a predecessor claim failed
    ORACLE_UNAVAILABLE = "oracle_unavailable"  # degraded mode


class CertificateStatus(str, Enum):
    VERIFIED = "verified"    # every claim renderable
    PARTIAL = "partial"      # some claims masked/rewritten
    BLOCKED = "blocked"      # output suppressed entirely
    DEGRADED = "degraded"    # oracles unavailable, soft pass under policy


class OracleReceipt(BaseModel):
    """Evidence that an oracle was consulted and what it returned."""
    model_config = ConfigDict(frozen=True)

    oracle_name: str
    queried_at: str                    # ISO-8601 UTC
    query: str                         # the question/SPARQL/text that was sent
    response_digest: str               # sha256 of the raw response body
    support: float                     # [0, 1] how strongly this oracle supports the claim
    contradiction: float = 0.0         # [0, 1] how strongly it refutes
    latency_ms: float = 0.0
    cache_hit: bool = False
    source_uri: Optional[str] = None   # permalink to the authoritative source
    error: Optional[str] = None        # if the oracle failed


class VerifiedClaim(BaseModel):
    """A single atomic claim with its verdict and evidence."""
    model_config = ConfigDict(frozen=False)

    claim_id: str                      # stable hash over (text, dependencies)
    text: str                          # atomic claim text
    depends_on: list[str] = Field(default_factory=list)  # predecessor claim_ids

    verdict: ClaimVerdict
    support_mass: float                # aggregated support [0, 1]
    contradiction_mass: float = 0.0    # aggregated contradiction [0, 1]

    oracle_receipts: list[OracleReceipt] = Field(default_factory=list)
    is_renderable: bool = True         # may appear in the final output

    # Optional diagnostic signals from Layer-3 (if the pipeline ran with deep mode)
    spectral_radius: Optional[float] = None
    steering_scale: Optional[float] = None
    sycophancy_delta: Optional[float] = None


class CertificatePayload(BaseModel):
    """The *signable* portion of the certificate. The signature covers exactly
    this (serialized via canonical JSON). `cert_id` is derived from it too.
    """
    model_config = ConfigDict(frozen=False)

    schema_version: str
    pipeline_version: str
    issued_at: str                     # ISO-8601 UTC

    tenant_id: str
    request_id: str                    # opaque correlation id
    model_id: str                      # upstream LLM identifier

    input_hash: str                    # sha256 of raw prompt bytes
    output_hash: str                   # sha256 of raw response bytes

    claims: list[VerifiedClaim]
    dag_edges: list[tuple[str, str]] = Field(default_factory=list)  # (pred_id, succ_id)

    overall_status: CertificateStatus
    renderable_text_hash: str          # sha256 of the text the gateway actually emitted

    oracles_used: list[str] = Field(default_factory=list)
    shadow_eval_sampled: bool = False
    degradations: list[str] = Field(default_factory=list)  # e.g. "wikidata_timeout"


class Certificate(BaseModel):
    """Full certificate = signable payload + signature envelope."""
    model_config = ConfigDict(frozen=False)

    cert_id: str                       # sha256 of canonical-JSON(payload)
    payload: CertificatePayload
    signature: str                     # base64 Ed25519 signature over cert_id
    signer_key_id: str                 # fingerprint of the signing key
    sig_algorithm: str = "ed25519"

    def is_renderable(self) -> bool:
        return self.payload.overall_status in (
            CertificateStatus.VERIFIED,
            CertificateStatus.PARTIAL,
            CertificateStatus.DEGRADED,
        )

    def renderable_claims(self) -> list[VerifiedClaim]:
        return [c for c in self.payload.claims if c.is_renderable]
