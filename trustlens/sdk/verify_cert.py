"""Offline certificate verification.

A cert is offline-verifiable: auditors can validate signatures without
contacting any TrustLens service. This file provides:
    - a `verify_certificate_file(path, public_key_pem)` one-liner
    - an `OfflineVerifier` class for batch workflows

Auditors typically pin the set of trusted signer key-ids per issuer so a
compromised key can be rotated without re-issuing all prior certificates
(prior certs remain valid under the retired key-id).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from trustlens.certificate.schema import Certificate
from trustlens.certificate.signer import (
    VerifyResult,
    load_public_key_pem,
    verify_certificate,
)


def verify_certificate_file(
    path: str | Path,
    public_key_pem: bytes,
    *,
    require_pipeline_version: Optional[str] = None,
    require_schema_version: Optional[str] = None,
    trusted_key_ids: Optional[set[str]] = None,
) -> VerifyResult:
    pub = load_public_key_pem(public_key_pem)
    cert = Certificate.model_validate_json(Path(path).read_bytes())
    return verify_certificate(
        cert, pub,
        require_pipeline_version=require_pipeline_version,
        require_schema_version=require_schema_version,
        trusted_key_ids=trusted_key_ids,
    )


class OfflineVerifier:
    """Stateful offline verifier for batch audit workflows."""

    def __init__(
        self,
        public_key_pem: bytes,
        *,
        trusted_key_ids: Optional[set[str]] = None,
        require_pipeline_version: Optional[str] = None,
        require_schema_version: Optional[str] = None,
    ):
        self._public = load_public_key_pem(public_key_pem)
        self._trusted_key_ids = trusted_key_ids
        self._require_pipeline = require_pipeline_version
        self._require_schema = require_schema_version

    def verify(self, cert: Certificate) -> VerifyResult:
        return verify_certificate(
            cert, self._public,
            require_pipeline_version=self._require_pipeline,
            require_schema_version=self._require_schema,
            trusted_key_ids=self._trusted_key_ids,
        )

    def verify_all(self, certs: list[Certificate]) -> dict:
        """Bulk verify a batch. Returns summary counts."""
        valid = 0
        invalid = 0
        pipeline_mismatch = 0
        schema_mismatch = 0
        reasons: dict[str, int] = {}
        for c in certs:
            r = self.verify(c)
            if r.valid:
                valid += 1
            else:
                invalid += 1
                reasons[r.reason] = reasons.get(r.reason, 0) + 1
            if not r.pipeline_version_match:
                pipeline_mismatch += 1
            if not r.schema_version_match:
                schema_mismatch += 1
        return {
            "total": len(certs),
            "valid": valid,
            "invalid": invalid,
            "pipeline_version_mismatch": pipeline_mismatch,
            "schema_version_mismatch": schema_mismatch,
            "invalid_reasons": reasons,
        }
