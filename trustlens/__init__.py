"""TrustLens — verifiable hallucination control for LLMs.

Public surface:
    from trustlens import TrustLens, Certificate, verify_certificate

Subpackages:
    gateway       — OpenAI-compatible HTTP gateway
    verifier      — stateless verification service (DAG + NLI + oracles)
    oracles       — pluggable grounding oracles (Wikidata, customer KB, ...)
    certificate   — signed, cryptographically verifiable trust certificates
    tenancy       — multi-tenant config, isolation, budgets
    robustness    — circuit breakers, deadlines, shadow eval
    observability — Prometheus metrics, OpenTelemetry spans
    sdk           — client library
    cli           — operator tooling
"""

from trustlens.version import __version__, PIPELINE_VERSION
from trustlens.certificate.schema import (
    Certificate,
    VerifiedClaim,
    ClaimVerdict,
    CertificateStatus,
)
from trustlens.certificate.signer import sign_certificate, verify_certificate
from trustlens.sdk.client import TrustLens

__all__ = [
    "__version__",
    "PIPELINE_VERSION",
    "TrustLens",
    "Certificate",
    "VerifiedClaim",
    "ClaimVerdict",
    "CertificateStatus",
    "sign_certificate",
    "verify_certificate",
]
