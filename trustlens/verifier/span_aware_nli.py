"""Span-aware NLI verifier.

Drop-in replacement for `LexicalNLI` that fixes a known false-positive in
the default lexical NLI:

    The verifier engine concatenates the top-N doc hits into one premise
    blob: "[1] doc-A | [2] doc-B | [3] doc-C". The default LexicalNLI runs
    negation-flip detection over that whole blob. If ANY of the unrelated
    docs (B or C) contains a negation cue ("not", "never", ...), it returns
    CONTRADICTION even though doc-A actually entails the hypothesis.

This SpanAwareNLI:
    1. Splits the premise on the engine's "[N] ... | [N+1] ..." separator.
    2. Runs the same overlap+negation heuristic *per span*.
    3. Returns the verdict from the BEST-MATCHING span (highest token
       overlap with the hypothesis), not the worst.

Same interface as `NLIVerifier` so you can pass it directly to
`VerifierEngine(nli=SpanAwareNLI())`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from trustlens.verifier.nli import NLIResult, NLIVerdict


_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-']+")
_NEGATION_CUES = (
    " not ", " never ", " no ", " false ", " incorrect ", " wrong ",
    " untrue ", " neither ", " contradict", " disprove",
    "not the ", "is not ", "are not ", "was not ", "were not ",
    "wasn't", "weren't", "isn't", "aren't", "doesn't", "didn't",
)
# Engine concat separator. Matches "[1] ", "[2] ", " | [3] " etc.
_SPAN_SPLIT = re.compile(r"(?:\s*\|\s*)?\[\d+\]\s*")


def _tokens(text: str) -> set[str]:
    return {
        m.group(0).lower()
        for m in _TOKEN.finditer(text)
        if len(m.group(0)) > 3
    }


def _has_negation(text: str) -> bool:
    text_low = " " + text.lower() + " "
    return any(cue in text_low for cue in _NEGATION_CUES)


def _overlap(a_tokens: set[str], b_tokens: set[str]) -> float:
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / max(len(b_tokens), 1)


@dataclass
class SpanAwareNLI:
    """Splits the premise into per-doc spans before applying NLI heuristics."""
    overlap_entail_threshold: float = 0.4
    overlap_contradiction_threshold: float = 0.5
    name: str = "span_aware_nli"

    def verify(self, premise: str, hypothesis: str) -> NLIResult:
        if not premise or not hypothesis:
            return NLIResult(
                NLIVerdict.NEUTRAL, 0.0,
                {"entailment": 0.0, "neutral": 1.0, "contradiction": 0.0},
            )

        spans = [s.strip() for s in _SPAN_SPLIT.split(premise) if s.strip()]
        if not spans:
            spans = [premise]

        hyp_tokens = _tokens(hypothesis)
        hyp_neg = _has_negation(hypothesis)

        # Score every span; pick the one with highest overlap with hypothesis
        scored: list[tuple[float, str]] = []
        for span in spans:
            sp_tokens = _tokens(span)
            ov = _overlap(sp_tokens, hyp_tokens)
            scored.append((ov, span))
        scored.sort(reverse=True)

        best_overlap, best_span = scored[0]
        prem_neg = _has_negation(best_span)

        # Negation flip + substantial overlap on the BEST-MATCHING span only
        if (prem_neg != hyp_neg) and best_overlap >= self.overlap_contradiction_threshold:
            return NLIResult(
                NLIVerdict.CONTRADICTION, 0.7,
                {"entailment": 0.1, "neutral": 0.2, "contradiction": 0.7},
            )

        if best_overlap >= self.overlap_entail_threshold and (prem_neg == hyp_neg):
            conf = min(0.95, best_overlap)
            return NLIResult(
                NLIVerdict.ENTAILMENT, conf,
                {"entailment": conf, "neutral": 1.0 - conf, "contradiction": 0.0},
            )

        return NLIResult(
            NLIVerdict.NEUTRAL, max(0.0, 1.0 - best_overlap),
            {
                "entailment": best_overlap * 0.5,
                "neutral": 1.0 - best_overlap,
                "contradiction": 0.0,
            },
        )
