#!/usr/bin/env python3
"""Calibration sweep — show how the lexical baseline behaves across tau values.

Runs the halu_eval suite at a grid of (tau, tau_prime) pairs and reports
block_rate vs false_block_rate. Useful for distinguishing benchmark *gaming*
(picking lenient gates) from *calibration* (picking the operating point that
matches what the scorer can actually express).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from trustlens.deep_inspector.benchmarks.datasets import HALU_EVAL
from trustlens.deep_inspector.benchmarks.scoring import block_decision
from trustlens.deep_inspector.engine import (
    DeepVerificationRequest,
    DeepVerifierEngine,
)
from trustlens.deep_inspector.ssh_adapter import StubSSHAdapter
from trustlens.deep_inspector.steering_adapter import StubSteeringAdapter
from trustlens.oracles.customer_kb import (
    CustomerKBOracle,
    KBDocument,
    LexicalKBIndex,
)
from trustlens.oracles.negation_aware import NegationAwareOracle
from trustlens.oracles.registry import OracleRegistry, OracleSelection
from trustlens.verifier.engine import VerifierEngine


def build_engine(items) -> DeepVerifierEngine:
    kb = LexicalKBIndex()
    seen = set()
    for it in items:
        for doc_id, text in it.kb_documents:
            if doc_id in seen:
                continue
            seen.add(doc_id)
            kb.add(KBDocument(doc_id=doc_id, text=text,
                              source_uri=f"kb://{doc_id}"),
                   tenant_id="bench")
    wrapped = NegationAwareOracle(
        inner=CustomerKBOracle(kb), name="customer_kb",
    )
    return DeepVerifierEngine(
        base=VerifierEngine(OracleRegistry([wrapped])),
        ssh=StubSSHAdapter(),
        steering=StubSteeringAdapter(),
    )


async def run_grid():
    items = HALU_EVAL
    n_halluc = sum(1 for i in items if i.label == "hallucinated")
    n_supported = len(items) - n_halluc

    grid_taus = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40]
    grid_tprime = [0.01, 0.02, 0.05]

    print(f"Calibrating on halu_eval ({n_halluc} hallucinated, {n_supported} supported)")
    print(f"{'tau':>6} {'tau_prime':>10}  block_rate  false_block_rate")
    print("-" * 50)

    rows = []
    for tau in grid_taus:
        for tprime in grid_tprime:
            if tprime >= tau:
                continue
            engine = build_engine(items)
            blocked_h = blocked_s = 0
            for i, item in enumerate(items):
                req = DeepVerificationRequest(
                    prompt=item.prompt,
                    response_text=item.response,
                    tenant_id="bench",
                    request_id=f"cal-{tau}-{tprime}-{i}",
                    model_id="bench-model",
                    tau=tau, tau_prime=tprime,
                    oracle_selection=OracleSelection(
                        priority_order=["customer_kb"], deadline_ms=300,
                    ),
                )
                res = await engine.verify(req)
                if block_decision(res.payload):
                    if item.label == "hallucinated": blocked_h += 1
                    else: blocked_s += 1
            block_rate = blocked_h / max(n_halluc, 1)
            false_block_rate = blocked_s / max(n_supported, 1)
            print(f"{tau:>6.2f} {tprime:>10.2f}  {block_rate:>10.3f}  {false_block_rate:>16.3f}")
            rows.append({
                "tau": tau, "tau_prime": tprime,
                "block_rate": round(block_rate, 3),
                "false_block_rate": round(false_block_rate, 3),
            })

    out = Path("./results/deep_inspector/calibration.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"halu_eval_grid": rows}, indent=2))
    print(f"\n[*] Wrote {out}")


if __name__ == "__main__":
    asyncio.run(run_grid())
