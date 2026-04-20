"""Numeric-aware NLI — wraps another NLIVerifier with number-mismatch detection.

Lexical NLI can't tell "Berlin Wall fell in 1991" apart from "Berlin Wall
fell in 1989" — token overlap is high, no negation flip. This wrapper adds
a complementary check:

    If the hypothesis contains a salient numeric token (year, integer,
    decimal, percentage) and the best-matching span of the premise contains
    a DIFFERENT salient numeric token of the same type, return CONTRADICTION.

Composition: NumericAwareNLI(inner=SpanAwareNLI()) gets you both fixes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from trustlens.verifier.nli import NLIResult, NLIVerdict, NLIVerifier


# Years 1000-2999, generic integers ≥ 2 digits, decimals, percentages
_NUM_RE = re.compile(
    r"\b(?:[12]\d{3}|\d{2,}(?:\.\d+)?|\d+%)\b"
)
# Common units that change a claim's meaning if the number changes
_UNIT_RE = re.compile(r"\b(?:meters?|km|kilometers?|seconds?|kg|kilograms?|years?|chromosomes?|degrees?)\b", re.I)


def _numbers(text: str) -> list[str]:
    return _NUM_RE.findall(text or "")


def _common_unit(claim: str, premise: str) -> bool:
    cu = set(m.group(0).lower() for m in _UNIT_RE.finditer(claim or ""))
    pu = set(m.group(0).lower() for m in _UNIT_RE.finditer(premise or ""))
    return bool(cu & pu)


def _is_year_like(s: str) -> bool:
    return bool(re.fullmatch(r"[12]\d{3}", s))


@dataclass
class NumericAwareNLI:
    """Wraps an inner NLIVerifier with number-mismatch override."""

    inner: NLIVerifier
    name: str = "numeric_aware_nli"

    def verify(self, premise: str, hypothesis: str) -> NLIResult:
        base = self.inner.verify(premise, hypothesis)

        hyp_nums = _numbers(hypothesis)
        if not hyp_nums:
            return base

        prem_nums = _numbers(premise)
        if not prem_nums:
            return base

        # Year-vs-year mismatch is a strong signal; also generic-num mismatch
        # is meaningful when the unit context overlaps.
        hyp_years = [n for n in hyp_nums if _is_year_like(n)]
        prem_years = [n for n in prem_nums if _is_year_like(n)]
        year_mismatch = bool(hyp_years) and bool(prem_years) and not (set(hyp_years) & set(prem_years))

        non_year_mismatch = False
        if not year_mismatch and _common_unit(hypothesis, premise):
            hyp_other = [n for n in hyp_nums if not _is_year_like(n)]
            prem_other = [n for n in prem_nums if not _is_year_like(n)]
            non_year_mismatch = (
                bool(hyp_other) and bool(prem_other)
                and not (set(hyp_other) & set(prem_other))
            )

        if year_mismatch or non_year_mismatch:
            return NLIResult(
                NLIVerdict.CONTRADICTION, 0.7,
                {"entailment": 0.05, "neutral": 0.25, "contradiction": 0.7},
            )

        return base
