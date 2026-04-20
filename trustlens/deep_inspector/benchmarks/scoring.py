"""Claim-aware scoring helpers.

The original suite-level scoring used `cert_status in (VERIFIED, PARTIAL)` as
the predicted-supported flag. That's coarse: for single-claim items, PARTIAL
with the lone claim masked is semantically *blocked*, not supported.

This module exposes a claim-aware predicate that asks the right question:
    "Did the gateway leave any of the response's substantive content
     renderable?"
"""

from __future__ import annotations

from dataclasses import dataclass

from trustlens.certificate.schema import (
    CertificatePayload,
    CertificateStatus,
    ClaimVerdict,
)


@dataclass
class ClaimAwareVerdict:
    """Per-item verdict from claim-aware scoring."""
    cert_status: str
    n_claims: int
    n_renderable: int
    n_unsupported_or_contradicted: int
    n_uncertain: int
    predicted_supported: bool
    """True iff at least one claim survived AND none of the substantive
    content was masked away. False iff every substantive claim was masked."""


def score_payload(payload: CertificatePayload) -> ClaimAwareVerdict:
    n_total = len(payload.claims)
    n_renderable = sum(1 for c in payload.claims if c.is_renderable)
    n_bad = sum(
        1 for c in payload.claims
        if c.verdict in (ClaimVerdict.UNSUPPORTED, ClaimVerdict.CONTRADICTED,
                         ClaimVerdict.DEPENDENCY_FAILED)
    )
    n_uncertain = sum(
        1 for c in payload.claims if c.verdict == ClaimVerdict.UNCERTAIN
    )

    # The harness emits one substantive claim per item by construction.
    # "supported" = at least one claim survived AND no claim was actively
    # contradicted/unsupported. We treat OUTPUT_BLOCKED status as the
    # authoritative override.
    if payload.overall_status == CertificateStatus.BLOCKED:
        predicted_supported = False
    elif n_total == 0:
        # No extractable claims → vacuously supported (nothing to refute).
        predicted_supported = True
    else:
        predicted_supported = (n_renderable > 0) and (n_bad == 0)

    return ClaimAwareVerdict(
        cert_status=payload.overall_status.value,
        n_claims=n_total,
        n_renderable=n_renderable,
        n_unsupported_or_contradicted=n_bad,
        n_uncertain=n_uncertain,
        predicted_supported=predicted_supported,
    )


def block_decision(payload: CertificatePayload) -> bool:
    """Did the gateway effectively block the substantive content of the response?"""
    if payload.overall_status == CertificateStatus.BLOCKED:
        return True
    if not payload.claims:
        return False
    n_renderable = sum(1 for c in payload.claims if c.is_renderable)
    n_bad = sum(
        1 for c in payload.claims
        if c.verdict in (ClaimVerdict.UNSUPPORTED, ClaimVerdict.CONTRADICTED,
                         ClaimVerdict.DEPENDENCY_FAILED)
    )
    # Treat as blocked if any substantive claim was rejected, OR if every
    # claim ended up non-renderable.
    return n_bad > 0 or n_renderable == 0
