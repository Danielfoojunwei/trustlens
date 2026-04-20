"""3D epistemic axes — internal × external × sycophancy.

Every cert payload carries enough information to reconstruct a point in a
three-dimensional confidence space:

    internal    in [0, 1]    — model's own stability (1.0 = stable)
                              sourced from Deep Inspector SSH when
                              available, else from NLI confidence
    external    in [0, 1]    — aggregate support mass from oracles
                              (customer KB, Wikidata, custom integrations)
    sycophancy  in [-1, 1]   — positive = model agreed with leading user
                              framing; 0 = neutral; negative = pushed back

The dashboard renders these three axes as:

    - three live gauges (one per axis)
    - a 3D-looking scatter over a rolling window
    - a per-claim radar chart for the current cert

``extract_axes`` is the single source of truth for how a cert collapses
to an ``AxisPoint``. Everything downstream (analytics, incidents, charts)
calls this helper.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from trustlens.certificate.schema import (
    CertificatePayload,
    ClaimVerdict,
    VerifiedClaim,
)


@dataclass
class AxisPoint:
    ts: float
    tenant_id: str
    cert_id: str
    internal: float           # [0, 1]
    external: float           # [0, 1]
    sycophancy: float         # [-1, 1]
    claim_count: int
    overall_status: str

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "tenant_id": self.tenant_id,
            "cert_id": self.cert_id,
            "internal": round(self.internal, 4),
            "external": round(self.external, 4),
            "sycophancy": round(self.sycophancy, 4),
            "claim_count": self.claim_count,
            "overall_status": self.overall_status,
        }


def _claim_internal(claim: VerifiedClaim) -> float:
    """NLI-derived internal confidence proxy for a claim.

    When Deep Inspector is enabled, we'd prefer the SSH-derived (1 − ρ)
    stability estimate carried on the payload's ``deep_inspector`` sidecar.
    Falls back to support_mass minus contradiction_mass clipped to [0, 1].
    """
    val = (claim.support_mass or 0.0) - (claim.contradiction_mass or 0.0)
    return max(0.0, min(1.0, val))


def _claim_sycophancy(claim: VerifiedClaim) -> float:
    """Claim-level sycophancy contribution.

    ``sycophancy_delta`` is in [0, 1] and positive means more sycophantic;
    absence means we didn't measure it on this claim, so contribute 0.
    """
    d = claim.sycophancy_delta
    if d is None:
        return 0.0
    # Keep sign so the axis can go negative in the rare case a pipeline
    # records a "pushed-back" delta.
    return max(-1.0, min(1.0, float(d)))


def extract_axes(payload: CertificatePayload, cert_id: str) -> AxisPoint:
    claims = payload.claims or []
    if not claims:
        return AxisPoint(
            ts=time.time(), tenant_id=payload.tenant_id, cert_id=cert_id,
            internal=0.0, external=0.0, sycophancy=0.0,
            claim_count=0, overall_status=payload.overall_status.value,
        )

    # External axis: mean support_mass across non-dependency-failed claims
    ext_vals = [c.support_mass or 0.0 for c in claims
                if c.verdict != ClaimVerdict.DEPENDENCY_FAILED]
    external = sum(ext_vals) / max(len(ext_vals), 1) if ext_vals else 0.0

    # Internal axis: prefer deep-inspector SSH stability if available
    di = getattr(payload, "deep_inspector", None)
    internal: float
    if di and isinstance(di, dict):
        snaps = di.get("ssh_snapshots") or []
        if snaps:
            rhos = [s.get("rho", 0.0) for s in snaps]
            # Stability = 1 − max(ρ), clipped to [0, 1]
            internal = max(0.0, min(1.0, 1.0 - max(rhos)))
        else:
            internal = sum(_claim_internal(c) for c in claims) / len(claims)
    else:
        internal = sum(_claim_internal(c) for c in claims) / len(claims)

    # Sycophancy axis: max absolute claim delta preserving sign
    syco_vals = [_claim_sycophancy(c) for c in claims]
    # choose the value with max abs(...) to surface the strongest signal
    sycophancy = max(syco_vals, key=lambda v: abs(v)) if syco_vals else 0.0

    return AxisPoint(
        ts=time.time(),
        tenant_id=payload.tenant_id,
        cert_id=cert_id,
        internal=float(internal),
        external=float(external),
        sycophancy=float(sycophancy),
        claim_count=len(claims),
        overall_status=payload.overall_status.value,
    )


class AxisLog:
    """Bounded ring buffer of AxisPoints for the dashboard.

    The dashboard polls ``/v1/admin/axes/recent`` to draw the gauges and
    the 3D scatter; this is the storage.
    """

    def __init__(self, capacity: int = 2000) -> None:
        self._buf: list[AxisPoint] = []
        self._capacity = capacity

    def record(self, point: AxisPoint) -> None:
        self._buf.append(point)
        if len(self._buf) > self._capacity:
            self._buf = self._buf[-self._capacity:]

    def recent(
        self,
        limit: int = 500,
        tenant_id: Optional[str] = None,
        since_s: Optional[float] = None,
    ) -> list[AxisPoint]:
        out: list[AxisPoint] = []
        cutoff = time.time() - since_s if since_s else None
        for p in reversed(self._buf):
            if tenant_id and p.tenant_id != tenant_id:
                continue
            if cutoff is not None and p.ts < cutoff:
                break
            out.append(p)
            if len(out) >= limit:
                break
        out.reverse()
        return out

    def summary(
        self, window_s: float = 300.0, tenant_id: Optional[str] = None
    ) -> dict:
        pts = self.recent(limit=10_000, tenant_id=tenant_id, since_s=window_s)
        if not pts:
            return {
                "n": 0, "window_s": window_s,
                "internal":   {"mean": 0.0, "min": 0.0, "max": 0.0},
                "external":   {"mean": 0.0, "min": 0.0, "max": 0.0},
                "sycophancy": {"mean": 0.0, "min": 0.0, "max": 0.0},
            }

        def stats(vals: list[float]) -> dict:
            return {
                "mean": round(sum(vals) / len(vals), 4),
                "min": round(min(vals), 4),
                "max": round(max(vals), 4),
            }
        return {
            "n": len(pts), "window_s": window_s,
            "internal":   stats([p.internal for p in pts]),
            "external":   stats([p.external for p in pts]),
            "sycophancy": stats([p.sycophancy for p in pts]),
        }

    def count(self) -> int:
        return len(self._buf)
