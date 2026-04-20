"""Shared item schema for every 10k-benchmark generator."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Optional


AXES: tuple[str, ...] = (
    "numeric_year_mismatch",
    "negation_flip",
    "cross_doc_contradiction",
    "anaphora_chain",
    "sycophancy_leading_cue",
    "prompt_injection",
    "jailbreak_suffix",
    "multi_turn_cascade",
    "pii_leak_trap",
    "span_isolation_compound",
)

# 1000 per axis → 10_000 total
AXIS_COUNTS: dict[str, int] = {a: 1000 for a in AXES}

TOTAL_N: int = sum(AXIS_COUNTS.values())
assert TOTAL_N == 10_000


Label = Literal["supported", "hallucinated", "adversarial", "neutral"]


@dataclass
class KBDoc:
    doc_id: str
    text: str
    source_uri: Optional[str] = None


@dataclass
class BenchItem:
    """One adversarial benchmark item.

    Fields:
        item_id      stable hex id (derived from axis + seed + index)
        axis         one of AXES
        prompt       what the user asks
        response     what the model replied (synthesized for offline eval)
        label        "supported" / "hallucinated" / "adversarial" / "neutral"
        kb_documents list of KBDoc to load into the tenant KB
        expected     dict of assertions the verifier should satisfy, e.g.
                       {"overall_status": "blocked"} or
                       {"claim_verdicts_any": ["contradicted"]}
        metadata     free-form diagnostics (template id, seed, sub-class)
    """
    item_id: str
    axis: str
    prompt: str
    response: str
    label: Label
    kb_documents: list[KBDoc] = field(default_factory=list)
    expected: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    # Some axes need multi-turn context; we fold it in via the prompt where
    # possible, but keep the raw chain available for TrustChain tests.
    chain_turns: Optional[list[dict]] = None

    def to_jsonable(self) -> dict:
        return {
            "item_id": self.item_id, "axis": self.axis,
            "prompt": self.prompt, "response": self.response,
            "label": self.label,
            "kb_documents": [asdict(d) for d in self.kb_documents],
            "expected": self.expected, "metadata": self.metadata,
            "chain_turns": self.chain_turns,
        }

    @classmethod
    def from_jsonable(cls, d: dict) -> "BenchItem":
        return cls(
            item_id=d["item_id"], axis=d["axis"],
            prompt=d["prompt"], response=d["response"],
            label=d["label"],
            kb_documents=[KBDoc(**x) for x in d.get("kb_documents", [])],
            expected=d.get("expected") or {},
            metadata=d.get("metadata") or {},
            chain_turns=d.get("chain_turns"),
        )
