"""Negation-aware oracle wrapper.

Wraps any underlying oracle and post-processes its response to handle the
contradicting-evidence-as-support failure mode:

    Claim:     "Sydney is the capital of Australia."
    KB hit:    "Canberra is the capital of Australia, NOT Sydney."
    Lexical:   high token overlap → high `support`.
    Reality:   the doc CONTRADICTS the claim.

This wrapper:
    1. Calls the underlying oracle.
    2. Inspects the returned `evidence` string for negation cues that occur
       near tokens shared with the claim.
    3. Splits the lexical signal into a (support, contradiction) pair so the
       verifier's aggregate stops counting contradiction as support.

Pure-Python, deterministic, dependency-free. Production deployments should
prefer a real NLI model — this wrapper is the "good-enough lexical guard"
that ships in the OSS verifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from trustlens.oracles.base import Oracle, OracleQuery, OracleResponse


_NEGATION_CUES = (
    " not ", " never ", " no ", " false ", " incorrect ", " wrong ",
    " untrue ", " neither ", " contradict", " disprove",
    "not the ", "is not ", "are not ", "was not ", "were not ",
    "wasn't", "weren't", "isn't", "aren't", "doesn't", "didn't",
)

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-']+")
_PROXIMITY_WINDOW_CHARS = 80


@dataclass
class NegationAwareOracle:
    """Composed oracle: inner oracle + negation-aware post-processing."""

    inner: Oracle
    name: str = ""
    contradiction_weight: float = 0.7
    """How much of the lexical 'support' to reassign as 'contradiction' when
    a negation cue is found near a shared token. 0.0 disables. 1.0 fully
    flips support → contradiction.
    """

    def __post_init__(self) -> None:
        if not self.name:
            self.name = getattr(self.inner, "name", "oracle")

    async def lookup(self, query: OracleQuery) -> OracleResponse:
        resp = await self.inner.lookup(query)
        if resp.support <= 0 and resp.contradiction <= 0:
            return resp
        evidence_low = (resp.evidence or "").lower()
        if not evidence_low:
            return resp

        contradiction_score = self._negation_score(query.claim_text, evidence_low)
        if contradiction_score <= 0:
            return resp

        # Re-attribute mass: shift `contradiction_score * support` from support
        # to contradiction.
        shift = min(resp.support, self.contradiction_weight * contradiction_score)
        new_support = max(0.0, resp.support - shift)
        new_contradiction = min(1.0, resp.contradiction + shift)

        return OracleResponse(
            oracle_name=self.name,
            evidence=resp.evidence,
            support=new_support,
            contradiction=new_contradiction,
            source_uri=resp.source_uri,
            response_digest=resp.response_digest,
            queried_at=resp.queried_at,
            latency_ms=resp.latency_ms,
            cache_hit=resp.cache_hit,
            error=resp.error,
            raw={
                **(resp.raw or {}),
                "negation_aware": {
                    "contradiction_score": round(contradiction_score, 3),
                    "support_before": round(resp.support, 3),
                    "support_after": round(new_support, 3),
                    "contradiction_before": round(resp.contradiction, 3),
                    "contradiction_after": round(new_contradiction, 3),
                },
            },
        )

    async def close(self) -> None:
        if hasattr(self.inner, "close"):
            await self.inner.close()  # type: ignore[func-returns-value]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @classmethod
    def _negation_score(cls, claim: str, evidence_low: str) -> float:
        """Return [0, 1] score for negation density near shared tokens.

        Heuristic: find each shared content token from the claim. For each
        occurrence in evidence, check whether any negation cue appears within
        a sliding window (±N chars). Return the fraction of token occurrences
        that have a nearby negation cue.
        """
        claim_tokens = {
            m.group(0).lower() for m in _TOKEN.finditer(claim)
            if len(m.group(0)) > 3
        }
        # Skip ultra-common words
        claim_tokens -= {
            "the", "this", "that", "these", "those", "with", "from",
            "what", "which", "have", "been", "their", "your", "into",
            "such", "than", "then", "when", "while", "very", "much",
            "many", "some", "more", "most", "less",
        }
        if not claim_tokens:
            return 0.0

        total_hits = 0
        flagged_hits = 0
        for tok in claim_tokens:
            for m in re.finditer(re.escape(tok), evidence_low):
                total_hits += 1
                lo = max(0, m.start() - _PROXIMITY_WINDOW_CHARS)
                hi = min(len(evidence_low), m.end() + _PROXIMITY_WINDOW_CHARS)
                window = evidence_low[lo:hi]
                if any(cue in window for cue in _NEGATION_CUES):
                    flagged_hits += 1
        if total_hits == 0:
            return 0.0
        return flagged_hits / total_hits
