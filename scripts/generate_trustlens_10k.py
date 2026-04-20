#!/usr/bin/env python3
"""Regenerate the committed ``trustlens_10k.jsonl.gz`` from the template
generators. Deterministic under ``--seed``.

Usage:
    python3 scripts/generate_trustlens_10k.py --seed 42
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from trustlens.benchmarks.trustlens_10k.manifest import (
    CORPUS_PATH, COMPLETE_MANIFEST, regenerate,
)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=str(CORPUS_PATH))
    args = p.parse_args(argv)

    out = Path(args.out)
    if args.out != str(CORPUS_PATH):
        # Custom path requested — write directly.
        from trustlens.benchmarks.trustlens_10k.generators import generate_all
        import gzip
        out.parent.mkdir(parents=True, exist_ok=True)
        items = generate_all(seed=args.seed)
        with gzip.open(out, "wt", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it.to_jsonable(), separators=(",", ":")) + "\n")
        n = len(items)
    else:
        _, n = regenerate(seed=args.seed)

    print(json.dumps({
        "manifest": COMPLETE_MANIFEST,
        "seed": args.seed,
        "path": str(out),
        "n_items": n,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
