"""Public loader for the committed 10k corpus + metadata."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Optional

from trustlens.benchmarks.trustlens_10k.schema import AXES, AXIS_COUNTS, BenchItem


CORPUS_PATH = Path(__file__).parent / "data" / "trustlens_10k.jsonl.gz"


COMPLETE_MANIFEST: dict = {
    "name": "TrustLens-10k",
    "version": "1.0.0",
    "n_items": sum(AXIS_COUNTS.values()),
    "n_axes": len(AXES),
    "axes": list(AXES),
    "axis_counts": dict(AXIS_COUNTS),
    "seed_default": 42,
    "reproducible": True,
    "generator_script": "scripts/generate_trustlens_10k.py",
    "runner_script":    "scripts/run_trustlens_10k.py",
}


def _ensure_generated() -> None:
    if CORPUS_PATH.exists():
        return
    # Lazy-generate from source. Used by tests so the corpus is always available.
    from trustlens.benchmarks.trustlens_10k.generators import generate_all
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    items = generate_all(seed=42)
    with gzip.open(CORPUS_PATH, "wt", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it.to_jsonable(), separators=(",", ":")) + "\n")


def load_corpus(axis: Optional[str] = None, limit: Optional[int] = None) -> list[BenchItem]:
    """Load the committed 10k corpus (optionally filtered by axis or capped)."""
    _ensure_generated()
    out: list[BenchItem] = []
    with gzip.open(CORPUS_PATH, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if axis is not None and d["axis"] != axis:
                continue
            out.append(BenchItem.from_jsonable(d))
            if limit is not None and len(out) >= limit:
                break
    return out


def regenerate(seed: int = 42) -> tuple[Path, int]:
    """Force-regenerate the committed corpus at the canonical path."""
    from trustlens.benchmarks.trustlens_10k.generators import generate_all
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    items = generate_all(seed=seed)
    with gzip.open(CORPUS_PATH, "wt", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it.to_jsonable(), separators=(",", ":")) + "\n")
    return CORPUS_PATH, len(items)
