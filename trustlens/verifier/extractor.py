"""Claim extraction.

Extracts atomic factual claims from a response, including dependency
detection for compositional verification. The default extractor is
rule-based and deterministic (sentence segmentation + anaphora heuristics).
For production, plug a structured-output LLM extractor via the same
`ClaimExtractor` protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from trustlens.verifier.claim_dag import Claim


class ClaimExtractor(Protocol):
    """Extract claims from a text, populating dependencies."""

    def extract(self, text: str, context: str = "") -> list[Claim]: ...


# ---------------------------------------------------------------------------
# Sentence segmentation (lightweight, deterministic, no external deps)
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_FACTUAL_MIN_LEN = 15
_OPINION_MARKERS = (
    "i think", "i believe", "i feel", "in my opinion",
    "perhaps", "maybe", "arguably", "probably",
)
_QUESTION_MARKERS = ("?",)


def _split_sentences(text: str) -> list[tuple[str, tuple[int, int]]]:
    """Split into (sentence, (start, end)) tuples."""
    out: list[tuple[str, tuple[int, int]]] = []
    cursor = 0
    for piece in _SENTENCE_END.split(text.strip()):
        piece = piece.strip()
        if not piece:
            continue
        start = text.find(piece, cursor)
        if start < 0:
            start = cursor
        end = start + len(piece)
        out.append((piece, (start, end)))
        cursor = end
    return out


def _is_factual(sentence: str) -> bool:
    s = sentence.strip().lower()
    if len(s) < _FACTUAL_MIN_LEN:
        return False
    if any(q in s for q in _QUESTION_MARKERS):
        return False
    if any(s.startswith(m) for m in _OPINION_MARKERS):
        return False
    return True


# ---------------------------------------------------------------------------
# Anaphora heuristic: "this river", "that president", "it", "these", ...
# ---------------------------------------------------------------------------

_ANAPHORA_PATTERNS = re.compile(
    r"\b(this|that|these|those|it|its|they|them|their|the former|the latter|"
    r"the (?:river|country|city|state|president|person|event|book|film|company|"
    r"substance|element|compound|molecule|animal|species|continent|ocean|"
    r"mountain|war|battle|result|number|formula|equation|planet|star|galaxy))\b",
    re.IGNORECASE,
)


class RegexExtractor:
    """Default extractor.

    Splits on sentence boundaries, filters questions/opinions/short fragments,
    and builds dependency edges from anaphora cues pointing at the most recent
    claim. This is intentionally conservative — better to miss a dependency
    than to falsely couple unrelated claims.
    """

    def __init__(self, min_chars: int = _FACTUAL_MIN_LEN):
        self.min_chars = min_chars

    def extract(self, text: str, context: str = "") -> list[Claim]:
        if not text or not text.strip():
            return []

        sentences = _split_sentences(text)
        claims: list[Claim] = []
        prev_id: str | None = None

        for sent_text, span in sentences:
            if not _is_factual(sent_text):
                continue
            deps: list[str] = []
            if prev_id and _ANAPHORA_PATTERNS.search(sent_text):
                deps.append(prev_id)
            claim = Claim.create(text=sent_text, depends_on=deps, span=span)
            claims.append(claim)
            prev_id = claim.claim_id
        return claims


# ---------------------------------------------------------------------------
# Structured-output LLM extractor (optional, for customers who want better
# decomposition). Requires a callable `llm(prompt) -> str` that returns JSON.
# ---------------------------------------------------------------------------

@dataclass
class LLMExtractor:
    """Wraps a structured-output LLM to extract claims.

    The caller supplies `llm_json_call(prompt) -> list[dict]` returning
    `[{"text": str, "depends_on": [str]}]`. TrustLens does not bundle an LLM
    — customers wire their own (OpenAI JSON mode, Anthropic tool-use, local
    model with grammar-constrained decoding).
    """

    llm_json_call: callable  # type: ignore[valid-type]
    prompt_template: str = (
        "Extract atomic factual claims from the text below as a JSON array of "
        'objects with fields "text" and "depends_on" (indices of claims this '
        'one depends on). Skip questions and opinions. '
        "Text:\n---\n{text}\n---\nJSON:"
    )

    def extract(self, text: str, context: str = "") -> list[Claim]:
        try:
            raw = self.llm_json_call(self.prompt_template.format(text=text))
        except Exception:
            # Fail closed: defer to the regex extractor
            return RegexExtractor().extract(text, context)

        claims_out: list[Claim] = []
        id_by_index: dict[int, str] = {}
        for i, item in enumerate(raw or []):
            if not isinstance(item, dict):
                continue
            t = str(item.get("text", "")).strip()
            if not t:
                continue
            dep_indices = item.get("depends_on", []) or []
            deps = [
                id_by_index[idx]
                for idx in dep_indices
                if isinstance(idx, int) and idx in id_by_index
            ]
            claim = Claim.create(text=t, depends_on=deps)
            id_by_index[i] = claim.claim_id
            claims_out.append(claim)
        return claims_out
