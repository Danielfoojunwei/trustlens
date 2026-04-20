"""Verifier engine — the orchestrator.

Glues together claim extraction → DAG construction → oracle fan-out → NLI →
epistemic routing → certificate assembly. Stateless, idempotent, safe to
scale horizontally.

Inputs:  prompt, response text, tenant config
Outputs: a Certificate (unsigned payload; the service signs it) + the
         renderable text with unsupported claims suppressed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from trustlens.certificate.schema import (
    CertificatePayload,
    CertificateStatus,
    ClaimVerdict,
    OracleReceipt,
    VerifiedClaim,
)
from trustlens.oracles.base import OracleQuery, OracleResponse
from trustlens.oracles.registry import OracleRegistry, OracleSelection
from trustlens.verifier.claim_dag import Claim, ClaimDAG
from trustlens.verifier.extractor import ClaimExtractor, RegexExtractor
from trustlens.verifier.nli import NLIVerdict, NLIVerifier, LexicalNLI
from trustlens.verifier.router import EpistemicRouter, Quadrant, RouteConfig
from trustlens.verifier.sycophancy import assess as _assess_sycophancy
from trustlens.version import CERT_SCHEMA_VERSION, PIPELINE_VERSION


@dataclass
class VerificationRequest:
    """Single verification job."""
    prompt: str
    response_text: str
    tenant_id: str
    request_id: str
    model_id: str = "unknown"
    oracle_selection: Optional[OracleSelection] = None
    route_config: Optional[RouteConfig] = None
    tau: float = 0.6                # verified threshold
    tau_prime: float = 0.3          # uncertain threshold
    deep_inspector_signals: dict = field(default_factory=dict)  # ρ, steering, syco


@dataclass
class VerificationResult:
    """Output of the engine. The caller (service) signs the payload."""
    payload: CertificatePayload
    renderable_text: str
    masked_claim_ids: list[str]
    oracle_latencies_ms: dict[str, float]


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VerifierEngine:
    """Orchestrates the verification pipeline."""

    def __init__(
        self,
        oracle_registry: OracleRegistry,
        extractor: Optional[ClaimExtractor] = None,
        nli: Optional[NLIVerifier] = None,
        router: Optional[EpistemicRouter] = None,
    ):
        self._oracles = oracle_registry
        self._extractor = extractor or RegexExtractor()
        self._nli = nli or LexicalNLI()
        self._router = router or EpistemicRouter()

    async def verify(self, req: VerificationRequest) -> VerificationResult:
        # 1. Extract claims
        claims = self._extractor.extract(req.response_text, context=req.prompt)

        # 2. Build DAG
        dag = ClaimDAG()
        for c in claims:
            dag.add(c)

        # 3. Oracle fan-out per claim (in topo order so dependencies resolve)
        oracle_latencies: dict[str, list[float]] = {}
        verified_claims: list[VerifiedClaim] = []
        verified_ids: set[str] = set()
        degradations: set[str] = set()

        selection = req.oracle_selection or OracleSelection(
            priority_order=self._oracles.names(),
            deadline_ms=250,
        )

        # Sycophancy assessment — request-level signal propagated to all claims.
        syco_result = _assess_sycophancy(
            prompt=req.prompt,
            response=req.response_text,
        )
        if syco_result.sycophancy_delta > 0:
            req.deep_inspector_signals["sycophancy_delta"] = syco_result.sycophancy_delta

        topo: list[Claim] = dag.topological_order()

        for claim in topo:
            # Short-circuit: if any ancestor failed, cascade-fail this claim
            ancestor_failed = any(
                aid not in verified_ids
                for aid in dag.ancestors(claim.claim_id)
            )
            if ancestor_failed and dag.ancestors(claim.claim_id):
                vc = self._make_claim(
                    claim, dag,
                    verdict=ClaimVerdict.DEPENDENCY_FAILED,
                    support=0.0, contradiction=0.0,
                    receipts=[], deep_signals=req.deep_inspector_signals,
                )
                vc.is_renderable = False
                verified_claims.append(vc)
                continue

            # Query oracles
            query = OracleQuery(
                claim_text=claim.text,
                context=req.prompt,
                tenant_id=req.tenant_id,
                deadline_ms=selection.deadline_ms,
            )
            responses = await self._oracles.query_many(query, selection)

            # Track latencies + degradations
            for r in responses:
                oracle_latencies.setdefault(r.oracle_name, []).append(r.latency_ms)
                if r.error:
                    degradations.add(f"{r.oracle_name}:{r.error}")

            # 4. NLI-refine each oracle response into support/contradiction
            receipts: list[OracleReceipt] = []
            for r in responses:
                if r.error or not r.evidence:
                    receipts.append(self._to_receipt(r))
                    continue
                nli_r = self._nli.verify(premise=r.evidence, hypothesis=claim.text)
                # Combine raw oracle signal with NLI evidence strength
                if nli_r.verdict == NLIVerdict.ENTAILMENT:
                    refined_support = min(1.0, r.support * 0.5 + nli_r.confidence * 0.7)
                    refined_contra = 0.0
                elif nli_r.verdict == NLIVerdict.CONTRADICTION:
                    refined_support = 0.0
                    refined_contra = min(1.0, nli_r.confidence)
                else:  # neutral
                    refined_support = r.support * 0.3
                    refined_contra = 0.0
                r.support = refined_support
                r.contradiction = refined_contra
                receipts.append(self._to_receipt(r))

            # 5. Aggregate oracle signal → support_mass / contradiction_mass
            support_mass = self._aggregate_support(receipts)
            contra_mass = self._aggregate_contradiction(receipts)

            # 6. Assign verdict
            if all(r.error for r in responses):
                verdict = ClaimVerdict.ORACLE_UNAVAILABLE
                is_renderable = False
            elif contra_mass >= max(req.tau_prime, support_mass):
                verdict = ClaimVerdict.CONTRADICTED
                is_renderable = False
            elif support_mass >= req.tau:
                verdict = ClaimVerdict.VERIFIED
                is_renderable = True
            elif support_mass >= req.tau_prime:
                verdict = ClaimVerdict.UNCERTAIN
                is_renderable = False
            elif all(not r.evidence for r in responses if not r.error):
                # No oracle returned any evidence text — the claim is absent
                # from all knowledge sources. Absent ≠ false; mark UNVERIFIABLE.
                verdict = ClaimVerdict.UNVERIFIABLE
                is_renderable = False
            else:
                verdict = ClaimVerdict.UNSUPPORTED
                is_renderable = False

            vc = self._make_claim(
                claim, dag,
                verdict=verdict,
                support=support_mass,
                contradiction=contra_mass,
                receipts=receipts,
                deep_signals=req.deep_inspector_signals,
            )
            vc.is_renderable = is_renderable
            verified_claims.append(vc)
            if verdict == ClaimVerdict.VERIFIED:
                verified_ids.add(claim.claim_id)

        # 7. Apply compositional closure — a claim is renderable only if
        #    all ancestors are also verified.
        renderable_ids = dag.renderable_closure(verified_ids)
        for vc in verified_claims:
            if vc.claim_id not in renderable_ids and vc.verdict == ClaimVerdict.VERIFIED:
                # downgrade: verified but a predecessor failed
                vc.verdict = ClaimVerdict.DEPENDENCY_FAILED
                vc.is_renderable = False

        # 8. Rebuild renderable text
        renderable_text, masked_ids = self._rebuild_text(
            req.response_text, verified_claims
        )

        # 9. Overall status
        overall = self._overall_status(verified_claims, degradations)

        # 10. Package the payload (UNSIGNED — service signs it)
        payload = CertificatePayload(
            schema_version=CERT_SCHEMA_VERSION,
            pipeline_version=PIPELINE_VERSION,
            issued_at=_now_iso(),
            tenant_id=req.tenant_id,
            request_id=req.request_id,
            model_id=req.model_id,
            input_hash=_sha256_hex(req.prompt),
            output_hash=_sha256_hex(req.response_text),
            claims=verified_claims,
            dag_edges=list(dag.edges()),
            overall_status=overall,
            renderable_text_hash=_sha256_hex(renderable_text),
            oracles_used=self._oracles.names(),
            degradations=sorted(degradations),
        )

        mean_latencies = {
            name: (sum(ls) / len(ls) if ls else 0.0)
            for name, ls in oracle_latencies.items()
        }

        return VerificationResult(
            payload=payload,
            renderable_text=renderable_text,
            masked_claim_ids=masked_ids,
            oracle_latencies_ms=mean_latencies,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_receipt(self, r: OracleResponse) -> OracleReceipt:
        return OracleReceipt(
            oracle_name=r.oracle_name,
            queried_at=r.queried_at or _now_iso(),
            query="",   # stored separately; keep receipt compact
            response_digest=r.response_digest,
            support=float(r.support),
            contradiction=float(r.contradiction),
            latency_ms=float(r.latency_ms),
            cache_hit=r.cache_hit,
            source_uri=r.source_uri,
            error=r.error,
        )

    def _aggregate_support(self, receipts: list[OracleReceipt]) -> float:
        live = [r for r in receipts if not r.error]
        if not live:
            return 0.0
        # Noisy-OR: evidence pieces are (approximately) independent.
        prob_not = 1.0
        for r in live:
            prob_not *= (1.0 - max(0.0, min(1.0, r.support)))
        return 1.0 - prob_not

    def _aggregate_contradiction(self, receipts: list[OracleReceipt]) -> float:
        live = [r for r in receipts if not r.error and r.contradiction > 0]
        if not live:
            return 0.0
        return max(r.contradiction for r in live)

    def _make_claim(
        self,
        claim: Claim,
        dag: ClaimDAG,
        verdict: ClaimVerdict,
        support: float,
        contradiction: float,
        receipts: list[OracleReceipt],
        deep_signals: dict,
    ) -> VerifiedClaim:
        return VerifiedClaim(
            claim_id=claim.claim_id,
            text=claim.text,
            depends_on=list(claim.depends_on),
            verdict=verdict,
            support_mass=round(float(support), 4),
            contradiction_mass=round(float(contradiction), 4),
            oracle_receipts=receipts,
            is_renderable=True,  # finalized later in compositional closure
            spectral_radius=deep_signals.get("spectral_radius"),
            steering_scale=deep_signals.get("steering_scale"),
            sycophancy_delta=deep_signals.get("sycophancy_delta"),
        )

    def _rebuild_text(
        self, original: str, claims: list[VerifiedClaim]
    ) -> tuple[str, list[str]]:
        """Reconstruct the response text with non-renderable claims masked.

        We do this span-based when spans are available; otherwise we just
        drop masked sentences.
        """
        masked_ids = [c.claim_id for c in claims if not c.is_renderable]
        if not masked_ids:
            return original, []

        renderable_texts = [c.text for c in claims if c.is_renderable]
        if not renderable_texts:
            return "", masked_ids
        return " ".join(renderable_texts), masked_ids

    def _overall_status(
        self, claims: list[VerifiedClaim], degradations: set[str]
    ) -> CertificateStatus:
        if not claims:
            # No extractable claims → treat as VERIFIED (e.g., a greeting).
            return CertificateStatus.VERIFIED
        renderable = [c for c in claims if c.is_renderable]
        if not renderable:
            return CertificateStatus.BLOCKED
        if degradations and len(renderable) == len(claims):
            return CertificateStatus.DEGRADED
        if len(renderable) == len(claims):
            return CertificateStatus.VERIFIED
        return CertificateStatus.PARTIAL
