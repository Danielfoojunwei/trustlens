#!/usr/bin/env python3
"""Run the TrustLens-10k adversarial benchmark against the in-process
verifier and emit a signed scorecard.

No LLM is called — the corpus carries pre-synthesized responses. The
verifier runs end-to-end (claim DAG → oracles → NLI stack → verdict),
exactly as it would on live traffic.

Usage:
    python3 scripts/run_trustlens_10k.py \
        --out-dir ./results/trustlens_10k \
        --signer-key ./.trustlens/signer.pem
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from trustlens.benchmarks.trustlens_10k import (
    AXES, AXIS_COUNTS, BenchItem, COMPLETE_MANIFEST,
    PER_AXIS_GATES, load_corpus,
)
from trustlens.benchmarks.trustlens_10k.gates import aggregate_pass
from trustlens.certificate.schema import ClaimVerdict
from trustlens.certificate.signer import KeyPair, canonical_json, payload_digest
from trustlens.deep_inspector.benchmarks.scoring import block_decision, score_payload
from trustlens.oracles.customer_kb import CustomerKBOracle, KBDocument, LexicalKBIndex
from trustlens.oracles.negation_aware import NegationAwareOracle
from trustlens.oracles.registry import OracleRegistry, OracleSelection
from trustlens.verifier.engine import VerificationRequest, VerifierEngine
from trustlens.verifier.numeric_aware_nli import NumericAwareNLI
from trustlens.verifier.span_aware_nli import SpanAwareNLI


@dataclass
class AxisResult:
    axis: str
    n: int
    n_adversarial: int
    n_supported: int
    blocked_adversarial: int
    false_blocks: int
    verdict_counts: dict
    block_rate: float
    false_block_rate: float
    passed: bool
    elapsed_s: float

    def to_dict(self) -> dict:
        return {
            "axis": self.axis,
            "n": self.n,
            "n_adversarial": self.n_adversarial,
            "n_supported": self.n_supported,
            "blocked_adversarial": self.blocked_adversarial,
            "false_blocks": self.false_blocks,
            "verdict_counts": self.verdict_counts,
            "block_rate": round(self.block_rate, 4),
            "false_block_rate": round(self.false_block_rate, 4),
            "passed": self.passed,
            "elapsed_s": round(self.elapsed_s, 3),
        }


def _build_engine_for_item(item: BenchItem) -> VerifierEngine:
    """One-KB-per-item isolation (matches how the gateway scopes by tenant)."""
    kb = LexicalKBIndex()
    for d in item.kb_documents:
        kb.add(KBDocument(doc_id=d.doc_id, text=d.text,
                          source_uri=d.source_uri or None,
                          metadata={}), tenant_id="bench")
    oracle = NegationAwareOracle(inner=CustomerKBOracle(kb), name="customer_kb")
    registry = OracleRegistry([oracle])
    return VerifierEngine(registry,
                          nli=NumericAwareNLI(inner=SpanAwareNLI()))


async def _run_one(item: BenchItem) -> tuple[bool, list[str]]:
    """Return (was_blocked, list_of_claim_verdicts)."""
    engine = _build_engine_for_item(item)
    req = VerificationRequest(
        prompt=item.prompt, response_text=item.response,
        tenant_id="bench", request_id=item.item_id,
        model_id="bench-synth", tau=0.30, tau_prime=0.10,
        oracle_selection=OracleSelection(
            priority_order=["customer_kb"], deadline_ms=300,
        ),
    )
    result = await engine.verify(req)
    blocked = block_decision(result.payload)
    verdicts = [
        c.verdict.value if hasattr(c.verdict, "value") else str(c.verdict)
        for c in result.payload.claims
    ]
    return blocked, verdicts


async def _run_axis(axis: str, items: list[BenchItem]) -> AxisResult:
    t0 = time.perf_counter()
    adv = [x for x in items if x.label in ("hallucinated", "adversarial")]
    sup = [x for x in items if x.label == "supported"]
    neutral = [x for x in items if x.label == "neutral"]

    blocked_adv = 0
    false_blocks = 0
    verdict_counter: Counter[str] = Counter()

    # Run items in small async batches to keep memory flat
    BATCH = 32
    for group, is_adv in ((adv, True), (sup, False), (neutral, False)):
        for i in range(0, len(group), BATCH):
            chunk = group[i:i + BATCH]
            results = await asyncio.gather(*[_run_one(x) for x in chunk])
            for (blocked, vs), it in zip(results, chunk):
                for v in vs:
                    verdict_counter[v] += 1
                if is_adv and blocked:
                    blocked_adv += 1
                elif (not is_adv) and blocked and it.label == "supported":
                    false_blocks += 1

    block_rate      = blocked_adv / max(len(adv), 1)
    false_block_rate = false_blocks / max(len(sup), 1)
    gate = PER_AXIS_GATES[axis]
    passed = (block_rate >= gate["min_block_rate"]
              and false_block_rate <= gate["max_false_block_rate"])
    return AxisResult(
        axis=axis, n=len(items),
        n_adversarial=len(adv), n_supported=len(sup),
        blocked_adversarial=blocked_adv,
        false_blocks=false_blocks,
        verdict_counts=dict(verdict_counter),
        block_rate=block_rate, false_block_rate=false_block_rate,
        passed=passed, elapsed_s=time.perf_counter() - t0,
    )


async def _run(axes: list[str], sample_limit: Optional[int]) -> dict:
    per_axis: dict[str, AxisResult] = {}
    for axis in axes:
        items = load_corpus(axis=axis, limit=sample_limit)
        print(f"→ {axis:<28} ({len(items)} items) ...", flush=True)
        r = await _run_axis(axis, items)
        per_axis[axis] = r
        print(f"  {axis:<28} block={r.block_rate:.3f} false={r.false_block_rate:.3f} "
              f"pass={r.passed}  ({r.elapsed_s:.1f}s)", flush=True)
    return per_axis


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="./results/trustlens_10k")
    p.add_argument("--signer-key", default="./.trustlens/signer.pem")
    p.add_argument("--axis", action="append", default=None,
                    help="restrict to one or more axes (repeatable)")
    p.add_argument("--sample-limit", type=int, default=None,
                    help="cap items per axis (for smoke runs)")
    args = p.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    axes = args.axis or list(AXES)
    started = time.time()
    per_axis = asyncio.run(_run(axes, args.sample_limit))

    # Aggregate
    per_axis_dicts = {a: r.to_dict() for a, r in per_axis.items()}
    overall_pass = all(r.passed for r in per_axis.values())
    total_items = sum(r.n for r in per_axis.values())

    # Sign the scorecard
    key_path = Path(args.signer_key)
    if key_path.exists():
        keypair = KeyPair.from_private_pem(key_path.read_bytes())
    else:
        keypair = KeyPair.generate()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(keypair.private_pem())
    pub_path = key_path.with_suffix(".pub.pem")
    pub_path.write_bytes(keypair.public_pem())

    payload = {
        "doc_type":    "trustlens.10k.scorecard",
        "doc_version": "1.0",
        "manifest":    COMPLETE_MANIFEST,
        "gates":       PER_AXIS_GATES,
        "started_at":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "duration_s":  round(time.time() - started, 3),
        "axes":        per_axis_dicts,
        "overall": {
            "total_items":   total_items,
            "n_axes_run":    len(per_axis),
            "all_passed":    overall_pass,
        },
    }
    digest = payload_digest(payload)
    from base64 import b64encode
    sig = keypair.private_key.sign(digest.encode())
    signed = {
        "payload":       payload,
        "scorecard_id":  digest,
        "signer_key_id": keypair.key_id,
        "signature":     b64encode(sig).decode(),
    }

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started))
    scorecard_file = out_dir / f"scorecard-{ts}.json"
    scorecard_file.write_text(json.dumps(signed, indent=2))
    summary_file = out_dir / f"scorecard-{ts}.summary.txt"
    summary = ["TrustLens-10k scorecard summary",
               f"  scorecard_id: {digest}",
               f"  signer_key_id: {keypair.key_id}",
               f"  duration_s: {payload['duration_s']}",
               f"  total_items: {total_items}",
               f"  overall_pass: {overall_pass}",
               "",
               "Per axis:"]
    for a in axes:
        r = per_axis.get(a)
        if not r: continue
        summary.append(
            f"  {a:<28}  block={r.block_rate:.3f}  "
            f"false={r.false_block_rate:.3f}  pass={r.passed}"
        )
    summary_file.write_text("\n".join(summary) + "\n")

    print()
    print("\n".join(summary))
    print(f"\nsigned scorecard: {scorecard_file}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
