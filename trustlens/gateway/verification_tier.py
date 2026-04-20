"""VerificationTier — per-request verification depth control.

Three tiers, ordered by computational cost:

    FAST      NLI-only (no oracle calls).        Target overhead: <30 ms
    STANDARD  NLI + customer KB oracle.          Target overhead: <100 ms
    DEEP      NLI + KB + Wikidata + Deep Insp.  Target overhead: <500 ms

The tier is set per-request via the `trustlens.verification_tier` field:

    {
      "model": "gpt-4o",
      "messages": [...],
      "trustlens": {"verification_tier": "standard"}
    }

Defaults fall back to the tenant's `TenantConfig.verify_deadline_ms` gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from trustlens.oracles.registry import OracleRegistry, OracleSelection
from trustlens.verifier.nli import LexicalNLI, NLIVerifier
from trustlens.verifier.span_aware_nli import SpanAwareNLI
from trustlens.verifier.numeric_aware_nli import NumericAwareNLI


class VerificationTier(str, Enum):
    FAST = "fast"           # NLI only — no oracle fan-out
    STANDARD = "standard"   # NLI + KB oracle(s)
    DEEP = "deep"           # NLI + KB + Wikidata + deep inspector


@dataclass
class TierConfig:
    """Resolved configuration for one request's verification path."""
    tier: VerificationTier
    oracle_names: list[str]          # oracles to query (empty = skip)
    deadline_ms: int                 # hard deadline for the whole verify step
    nli: NLIVerifier                 # NLI implementation to inject


def resolve_tier(
    requested: Optional[str],
    available_oracles: list[str],
    tenant_deadline_ms: int = 250,
) -> TierConfig:
    """Map a requested tier string to a concrete TierConfig.

    Args:
        requested:          Tier name from the request body, or None.
        available_oracles:  Names of oracles registered in the tenant's registry.
        tenant_deadline_ms: Fallback deadline from TenantConfig.
    """
    try:
        tier = VerificationTier(requested.lower()) if requested else VerificationTier.STANDARD
    except ValueError:
        tier = VerificationTier.STANDARD

    if tier == VerificationTier.FAST:
        return TierConfig(
            tier=tier,
            oracle_names=[],          # skip all oracle calls
            deadline_ms=min(30, tenant_deadline_ms),
            nli=SpanAwareNLI(),       # fast lexical NLI, no transformer overhead
        )

    if tier == VerificationTier.STANDARD:
        # Only customer KB oracles — filter out wikidata for latency
        kb_oracles = [n for n in available_oracles if "wikidata" not in n.lower()]
        return TierConfig(
            tier=tier,
            oracle_names=kb_oracles or available_oracles,
            deadline_ms=min(100, tenant_deadline_ms),
            nli=NumericAwareNLI(inner=SpanAwareNLI()),
        )

    # DEEP: all oracles, transformer NLI if available
    deep_nli: NLIVerifier
    try:
        from trustlens.verifier.transformer_nli import TransformerNLI  # heavy import
        deep_nli = TransformerNLI()
    except Exception:
        deep_nli = NumericAwareNLI(inner=SpanAwareNLI())

    return TierConfig(
        tier=tier,
        oracle_names=available_oracles,
        deadline_ms=min(500, tenant_deadline_ms),
        nli=deep_nli,
    )


def oracle_selection_for(config: TierConfig) -> OracleSelection:
    """Build an OracleSelection from a resolved TierConfig."""
    return OracleSelection(
        priority_order=config.oracle_names,
        deadline_ms=config.deadline_ms,
    )
