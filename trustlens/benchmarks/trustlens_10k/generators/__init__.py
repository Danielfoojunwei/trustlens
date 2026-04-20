"""Per-axis generators for the TrustLens-10k corpus.

Each generator exposes a ``generate(seed: int) -> list[BenchItem]`` of
length 1000. The package-level ``generate_all`` fan-outs across axes.
"""
from __future__ import annotations

from trustlens.benchmarks.trustlens_10k.schema import AXES, AXIS_COUNTS, BenchItem

# Individual axes
from trustlens.benchmarks.trustlens_10k.generators import (
    anaphora as _anaphora,
    compound as _compound,
    cross_doc as _cross_doc,
    jailbreak as _jailbreak,
    multi_turn as _multi_turn,
    negation as _negation,
    numeric as _numeric,
    pii_trap as _pii_trap,
    prompt_injection as _prompt_injection,
    sycophancy as _sycophancy,
)


GENERATORS = {
    "numeric_year_mismatch":   _numeric.generate,
    "negation_flip":           _negation.generate,
    "cross_doc_contradiction": _cross_doc.generate,
    "anaphora_chain":          _anaphora.generate,
    "sycophancy_leading_cue":  _sycophancy.generate,
    "prompt_injection":        _prompt_injection.generate,
    "jailbreak_suffix":        _jailbreak.generate,
    "multi_turn_cascade":      _multi_turn.generate,
    "pii_leak_trap":           _pii_trap.generate,
    "span_isolation_compound": _compound.generate,
}

assert set(GENERATORS) == set(AXES), (
    f"GENERATORS keys {sorted(GENERATORS)} != AXES {sorted(AXES)}"
)


def generate_axis(axis: str, seed: int = 42) -> list[BenchItem]:
    if axis not in GENERATORS:
        raise ValueError(f"unknown axis: {axis}")
    items = GENERATORS[axis](seed=seed)
    target = AXIS_COUNTS[axis]
    assert len(items) == target, (
        f"{axis} produced {len(items)} items, expected {target}"
    )
    return items


def generate_all(seed: int = 42) -> list[BenchItem]:
    out: list[BenchItem] = []
    for axis in AXES:
        out.extend(generate_axis(axis, seed=seed))
    return out


__all__ = ["GENERATORS", "generate_axis", "generate_all"]
