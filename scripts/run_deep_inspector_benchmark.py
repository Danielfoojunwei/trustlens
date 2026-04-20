#!/usr/bin/env python3
"""Run the full Deep Inspector verification benchmark suite.

Usage:
    python3 scripts/run_deep_inspector_benchmark.py \
        --signer-key ./.trustlens/signer.pem \
        --out-dir ./results/deep_inspector

Produces:
    results/deep_inspector/scorecard-<timestamp>.json   — signed scorecard
    results/deep_inspector/scorecard-<timestamp>.summary.txt
    results/deep_inspector/signer.pub.pem               — public key for verify

Exit code: 0 if all suites pass SLA gates, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from trustlens.certificate.signer import KeyPair, load_public_key_pem
from trustlens.deep_inspector.benchmarks import (
    BenchmarkSuite,
    Scorecard,
    TieredBenchmarkSuite,
    VerifierTier,
    sign_scorecard,
    verify_scorecard,
)
from trustlens.deep_inspector.benchmarks.tiered_v2 import TieredBenchmarkSuiteV2
from trustlens.deep_inspector.benchmarks.tiered_v3 import TieredBenchmarkSuiteV3


def _print_run(run, indent="  ") -> None:
    status = "PASS" if run.passed else "FAIL"
    line = f"{indent}[{status}] {run.suite}  ({run.n_items} items, {run.elapsed_s}s)"
    print(line)
    for k, v in run.metrics.items():
        if isinstance(v, (list, dict)):
            print(f"{indent}    {k}: {json.dumps(v)}")
        else:
            print(f"{indent}    {k}: {v}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--signer-key", default="./.trustlens/signer.pem",
                   help="Path to Ed25519 private key PEM (created if missing)")
    p.add_argument("--out-dir", default="./results/deep_inspector")
    p.add_argument("--sample-limit", type=int, default=None,
                   help="Cap items per suite (for smoke runs)")
    p.add_argument("--suite", action="append", default=None,
                   help="Restrict to suites: truthful_qa halu_eval pareto chain chaos")
    p.add_argument("--tier", choices=["lexical", "nli", "deep", "legacy"],
                   default="lexical",
                   help="SLA gate tier. 'legacy' uses the original BenchmarkSuite "
                        "with its hardcoded gates.")
    p.add_argument("--suite-version", choices=["v1", "v2", "v3"], default="v3",
                   help="v1=plain TieredBenchmarkSuite, v2=+SpanAwareNLI, "
                        "v3=v2+NumericAwareNLI+verified-fraction Pareto.")
    args = p.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Get/create signer
    key_path = Path(args.signer_key)
    if key_path.exists():
        keypair = KeyPair.from_private_pem(key_path.read_bytes())
        print(f"[*] Using existing signer: {keypair.key_id}")
    else:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        keypair = KeyPair.generate()
        key_path.write_bytes(keypair.private_pem())
        (key_path.with_suffix(".pub.pem")).write_bytes(keypair.public_pem())
        print(f"[*] Generated new signer: {keypair.key_id}")

    pub_path = out_dir / "signer.pub.pem"
    pub_path.write_bytes(keypair.public_pem())

    # 2. Run suites
    print(f"[*] Running Deep Inspector benchmark suite (tier={args.tier})...")
    t0 = time.perf_counter()
    if args.tier == "legacy":
        suite = BenchmarkSuite(
            sample_limit_per_suite=args.sample_limit,
            suite_filter=set(args.suite) if args.suite else None,
        )
    else:
        suite_class = {
            "v1": TieredBenchmarkSuite,
            "v2": TieredBenchmarkSuiteV2,
            "v3": TieredBenchmarkSuiteV3,
        }[args.suite_version]
        suite = suite_class(
            tier=VerifierTier(args.tier),
            sample_limit_per_suite=args.sample_limit,
            suite_filter=set(args.suite) if args.suite else None,
        )
    scorecard: Scorecard = asyncio.run(suite.run_all())
    elapsed = time.perf_counter() - t0
    print(f"[*] Suites complete in {elapsed:.2f}s")

    # 3. Sign + persist
    sign_scorecard(scorecard, keypair)

    ts = int(time.time())
    json_path = out_dir / f"scorecard-{ts}.json"
    json_path.write_text(
        json.dumps(scorecard.to_dict(), indent=2, default=str)
    )
    print(f"[*] Wrote {json_path}")

    # 4. Print summary
    print("=" * 70)
    print(f"DEEP INSPECTOR BENCHMARK — {scorecard.issued_at}")
    print(f"trustlens={scorecard.trustlens_version}  pipeline={scorecard.pipeline_version}")
    print(f"scorecard_id={scorecard.scorecard_id[:32]}...")
    print(f"signer_key_id={scorecard.signer_key_id}")
    print("-" * 70)
    for run in scorecard.runs:
        _print_run(run)
    print("-" * 70)
    print(f"Aggregate: {scorecard.aggregate}")
    print(f"OVERALL: {'PASS' if scorecard.overall_passed else 'FAIL'}")
    print("=" * 70)

    # 5. Verify the signed scorecard offline as a self-check
    pub = load_public_key_pem(pub_path.read_bytes())
    sig_ok = verify_scorecard(scorecard, pub)
    print(f"[*] Offline signature verification: {'OK' if sig_ok else 'FAIL'}")

    # 6. Write a human-readable summary
    summary_path = out_dir / f"scorecard-{ts}.summary.txt"
    lines: list[str] = []
    lines.append(f"DEEP INSPECTOR BENCHMARK SUMMARY")
    lines.append(f"  issued_at={scorecard.issued_at}")
    lines.append(f"  scorecard_id={scorecard.scorecard_id}")
    lines.append(f"  signer_key_id={scorecard.signer_key_id}")
    lines.append(f"  overall_passed={scorecard.overall_passed}")
    lines.append("")
    for run in scorecard.runs:
        lines.append(f"  {'PASS' if run.passed else 'FAIL'} {run.suite}  ({run.n_items} items, {run.elapsed_s}s)")
        for k, v in run.metrics.items():
            lines.append(f"    {k}: {v}")
        lines.append("")
    summary_path.write_text("\n".join(lines))
    print(f"[*] Wrote {summary_path}")

    return 0 if (scorecard.overall_passed and sig_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
