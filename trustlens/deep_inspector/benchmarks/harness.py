"""Benchmark harness — orchestrates suites, computes metrics, signs scorecard.

The harness wires:
    - LexicalKBIndex      — populated per-suite from datasets.py
    - CustomerKBOracle    — fan-out target for the verifier
    - VerifierEngine      — base claim-DAG verification
    - DeepVerifierEngine  — wraps base with SSH + steering + chain
    - StubSSHAdapter      — deterministic spectral signal
    - StubSteeringAdapter — records engage/disengage events

Output: a `Scorecard` with one `BenchmarkRun` per suite. The scorecard can
be signed with the operator's TrustLens key and verified offline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from trustlens.certificate.schema import (
    CertificatePayload,
    CertificateStatus,
    ClaimVerdict,
    VerifiedClaim,
)
from trustlens.certificate.signer import (
    KeyPair,
    canonical_json,
    sign_certificate,
)
from trustlens.deep_inspector.agentic_chain import TrustChain
from trustlens.deep_inspector.benchmarks.datasets import (
    CHAIN_TASKS,
    HALU_EVAL,
    PARETO_PROMPTS,
    TRUTHFUL_QA,
    BenchItem,
    ChainTask,
)
from trustlens.deep_inspector.engine import (
    DeepVerificationRequest,
    DeepVerifierEngine,
)
from trustlens.deep_inspector.ssh_adapter import StubSSHAdapter
from trustlens.deep_inspector.steering_adapter import (
    SteeringConfig,
    StubSteeringAdapter,
)
from trustlens.oracles.customer_kb import (
    CustomerKBOracle,
    KBDocument,
    LexicalKBIndex,
)
from trustlens.oracles.negation_aware import NegationAwareOracle
from trustlens.oracles.registry import OracleRegistry, OracleSelection
from trustlens.robustness.deadline import Deadline, DeadlineExceeded
from trustlens.verifier.engine import VerifierEngine
from trustlens.version import (
    CERT_SCHEMA_VERSION,
    PIPELINE_VERSION,
    __version__,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkRun:
    """One suite's result."""
    suite: str
    n_items: int
    metrics: dict[str, Any]
    samples: list[dict] = field(default_factory=list)
    elapsed_s: float = 0.0
    passed: bool = False


@dataclass
class Scorecard:
    """Aggregate result over all suites + overall pass/fail + sign envelope."""
    trustlens_version: str
    pipeline_version: str
    cert_schema_version: str
    issued_at: str
    runs: list[BenchmarkRun]
    overall_passed: bool
    aggregate: dict[str, Any] = field(default_factory=dict)
    signature: Optional[str] = None
    signer_key_id: Optional[str] = None
    scorecard_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "trustlens_version": self.trustlens_version,
            "pipeline_version": self.pipeline_version,
            "cert_schema_version": self.cert_schema_version,
            "issued_at": self.issued_at,
            "overall_passed": self.overall_passed,
            "aggregate": self.aggregate,
            "runs": [asdict(r) for r in self.runs],
            "scorecard_id": self.scorecard_id,
            "signer_key_id": self.signer_key_id,
            "signature": self.signature,
        }


# ---------------------------------------------------------------------------
# SLA gates — what counts as "passing"
# ---------------------------------------------------------------------------

SLA_GATES = {
    "truthful_qa": {"min_precision": 0.85, "min_recall": 0.70, "max_p99_ms": 400},
    "halu_eval": {"min_block_rate": 0.70, "max_false_block_rate": 0.30},
    "pareto": {"min_capability_at_alpha_1": 0.50, "max_capability_drop_per_alpha": 0.40},
    "chain": {"min_cascade_detection": 0.80},
    "chaos": {"min_graceful_degradation_rate": 0.90},
}


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------

class BenchmarkSuite:
    """Run all five suites and produce a signed scorecard."""

    def __init__(
        self,
        sample_limit_per_suite: Optional[int] = None,
        suite_filter: Optional[set[str]] = None,
    ):
        self._sample_limit = sample_limit_per_suite
        self._suite_filter = suite_filter

    async def run_all(self) -> Scorecard:
        runs: list[BenchmarkRun] = []
        for name, fn in [
            ("truthful_qa", self._run_truthful_qa),
            ("halu_eval", self._run_halu_eval),
            ("pareto", self._run_pareto),
            ("chain", self._run_chain),
            ("chaos", self._run_chaos),
        ]:
            if self._suite_filter and name not in self._suite_filter:
                continue
            t0 = time.perf_counter()
            run = await fn()
            run.elapsed_s = round(time.perf_counter() - t0, 3)
            runs.append(run)

        overall = all(r.passed for r in runs)
        agg = {
            "n_suites": len(runs),
            "n_passed": sum(1 for r in runs if r.passed),
            "total_items": sum(r.n_items for r in runs),
            "total_elapsed_s": round(sum(r.elapsed_s for r in runs), 3),
        }
        from datetime import datetime, timezone
        return Scorecard(
            trustlens_version=__version__,
            pipeline_version=PIPELINE_VERSION,
            cert_schema_version=CERT_SCHEMA_VERSION,
            issued_at=datetime.now(timezone.utc).isoformat(),
            runs=runs,
            overall_passed=overall,
            aggregate=agg,
        )

    # ------------------------------------------------------------------
    # Suite 1: TruthfulQA-style precision/recall
    # ------------------------------------------------------------------

    async def _run_truthful_qa(self) -> BenchmarkRun:
        items = self._sliced(TRUTHFUL_QA)
        engine = self._build_engine(items)

        latencies_ms: list[float] = []
        tp = fp = fn = tn = 0
        samples: list[dict] = []

        for i, item in enumerate(items):
            req = DeepVerificationRequest(
                prompt=item.prompt,
                response_text=item.response,
                tenant_id="bench",
                request_id=f"tqa-{i}",
                model_id="bench-model",
                tau=0.30,
                tau_prime=0.05,
                oracle_selection=OracleSelection(
                    priority_order=["customer_kb"], deadline_ms=300,
                ),
            )
            t0 = time.perf_counter()
            res = await engine.verify(req)
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)

            cert_status = res.payload.overall_status
            predicted_supported = cert_status in (
                CertificateStatus.VERIFIED, CertificateStatus.PARTIAL
            )
            actually_supported = item.label == "supported"

            if actually_supported and predicted_supported: tp += 1
            elif actually_supported and not predicted_supported: fn += 1
            elif (not actually_supported) and (not predicted_supported): tn += 1
            else: fp += 1

            samples.append({
                "prompt": item.prompt[:60],
                "label": item.label,
                "cert_status": cert_status.value,
                "n_claims": len(res.payload.claims),
                "n_renderable": sum(1 for c in res.payload.claims if c.is_renderable),
                "ssh_critical": sum(
                    1 for a in res.ssh_alarms if a["severity"] == "critical"
                ),
            })

        metrics = self._classification_metrics(tp, fp, fn, tn, latencies_ms)
        gate = SLA_GATES["truthful_qa"]
        passed = (
            metrics["precision"] >= gate["min_precision"]
            and metrics["recall"] >= gate["min_recall"]
            and metrics["latency_p99_ms"] <= gate["max_p99_ms"]
        )
        return BenchmarkRun(
            suite="truthful_qa", n_items=len(items),
            metrics=metrics, samples=samples, passed=passed,
        )

    # ------------------------------------------------------------------
    # Suite 2: HaluEval-style adversarial — must block hallucinations
    # ------------------------------------------------------------------

    async def _run_halu_eval(self) -> BenchmarkRun:
        items = self._sliced(HALU_EVAL)
        engine = self._build_engine(items)

        n_halluc = sum(1 for i in items if i.label == "hallucinated")
        n_supported = len(items) - n_halluc
        blocked_halluc = 0
        false_blocks = 0
        samples: list[dict] = []

        for i, item in enumerate(items):
            req = DeepVerificationRequest(
                prompt=item.prompt,
                response_text=item.response,
                tenant_id="bench",
                request_id=f"hal-{i}",
                model_id="bench-model",
                tau=0.40,
                tau_prime=0.10,
                oracle_selection=OracleSelection(
                    priority_order=["customer_kb"], deadline_ms=300,
                ),
            )
            res = await engine.verify(req)
            blocked = res.payload.overall_status in (
                CertificateStatus.BLOCKED,
            ) or any(not c.is_renderable for c in res.payload.claims)

            if item.label == "hallucinated" and blocked:
                blocked_halluc += 1
            if item.label == "supported" and blocked:
                false_blocks += 1
            samples.append({
                "prompt": item.prompt[:60],
                "label": item.label,
                "blocked": blocked,
                "cert_status": res.payload.overall_status.value,
            })

        block_rate = blocked_halluc / max(n_halluc, 1)
        false_block_rate = false_blocks / max(n_supported, 1)
        gate = SLA_GATES["halu_eval"]
        passed = (
            block_rate >= gate["min_block_rate"]
            and false_block_rate <= gate["max_false_block_rate"]
        )
        return BenchmarkRun(
            suite="halu_eval", n_items=len(items),
            metrics={
                "block_rate": round(block_rate, 3),
                "false_block_rate": round(false_block_rate, 3),
                "n_hallucinated": n_halluc,
                "n_supported": n_supported,
                "blocked_hallucinations": blocked_halluc,
                "false_blocks": false_blocks,
            },
            samples=samples, passed=passed,
        )

    # ------------------------------------------------------------------
    # Suite 3: Pareto sweep — capability vs steering alpha
    # ------------------------------------------------------------------

    async def _run_pareto(self) -> BenchmarkRun:
        items = self._sliced(PARETO_PROMPTS)
        alphas = [0.0, 1.0, 2.5, 5.0]

        per_alpha: list[dict] = []
        capabilities: list[float] = []

        for alpha in alphas:
            engine = self._build_engine(
                items, steering_alpha=alpha, force_engage=alpha > 0,
            )
            n_correct = 0
            for i, item in enumerate(items):
                req = DeepVerificationRequest(
                    prompt=item.prompt,
                    response_text=item.response,
                    tenant_id="bench",
                    request_id=f"par-{alpha}-{i}",
                    model_id="bench-model",
                    tau=0.30, tau_prime=0.05,
                    oracle_selection=OracleSelection(
                        priority_order=["customer_kb"], deadline_ms=300,
                    ),
                )
                res = await engine.verify(req)
                # Capability proxy: model says "supported" → cert keeps content
                kept = res.payload.overall_status in (
                    CertificateStatus.VERIFIED, CertificateStatus.PARTIAL
                )
                if kept and item.label == "supported":
                    n_correct += 1

            cap = n_correct / max(len(items), 1)
            capabilities.append(cap)
            per_alpha.append({
                "alpha": alpha,
                "capability": round(cap, 3),
                "n_correct": n_correct,
                "n_total": len(items),
            })

        # Capability drop per unit alpha (linear approx between alpha=0 and alpha=max)
        cap_drop_per_alpha = (capabilities[0] - capabilities[-1]) / (alphas[-1] or 1)
        gate = SLA_GATES["pareto"]
        cap_at_alpha_1 = next((p["capability"] for p in per_alpha if p["alpha"] == 1.0), 0)
        passed = (
            cap_at_alpha_1 >= gate["min_capability_at_alpha_1"]
            and cap_drop_per_alpha <= gate["max_capability_drop_per_alpha"]
        )
        return BenchmarkRun(
            suite="pareto", n_items=len(items) * len(alphas),
            metrics={
                "alphas": alphas,
                "capability_per_alpha": capabilities,
                "capability_drop_per_alpha": round(cap_drop_per_alpha, 4),
            },
            samples=per_alpha, passed=passed,
        )

    # ------------------------------------------------------------------
    # Suite 4: Multi-turn chain cascade detection
    # ------------------------------------------------------------------

    async def _run_chain(self) -> BenchmarkRun:
        tasks = list(CHAIN_TASKS)
        if self._sample_limit is not None:
            tasks = tasks[: self._sample_limit]

        cascades_detected = 0
        n_cascade_tasks = 0
        samples: list[dict] = []

        for task in tasks:
            engine = self._build_engine_from_chain(task)
            chain = TrustChain()
            prev_claim_ids: list[str] = []
            for step_idx, step in enumerate(task.steps):
                # Build per-claim parents: every new claim depends on every prev claim
                # (coarse but realistic for the agentic-chain stress case)
                req_parents: dict[str, list[str]] = {}
                req = DeepVerificationRequest(
                    prompt=step.prompt,
                    response_text=step.response,
                    tenant_id="bench",
                    request_id=f"chain-{task.name}-{step_idx}",
                    model_id="bench-model",
                    tau=0.30, tau_prime=0.05,
                    oracle_selection=OracleSelection(
                        priority_order=["customer_kb"], deadline_ms=300,
                    ),
                    turn_idx=step_idx,
                    chain=chain,
                    parent_claims=req_parents,
                )
                res = await engine.verify(req)
                # After we know this turn's claim_ids, edges are added by the engine
                cur_claim_ids = [c.claim_id for c in res.payload.claims]
                if prev_claim_ids:
                    for cid in cur_claim_ids:
                        chain.add_turn(
                            turn_idx=step_idx,
                            claim_ids=[cid],
                            parents={cid: prev_claim_ids},
                        )
                prev_claim_ids = cur_claim_ids

            cascade = chain.cascade_summary()
            has_cascade = any(
                step.label == "hallucinated" for step in task.steps
            )
            detected = cascade["first_unreliable_turn"] is not None
            if has_cascade:
                n_cascade_tasks += 1
                if detected:
                    cascades_detected += 1
            samples.append({
                "task": task.name,
                "has_cascade": has_cascade,
                "detected": detected,
                "first_unreliable_turn": cascade["first_unreliable_turn"],
                "blast_radius": cascade["cascade_blast_radius"],
            })

        detection_rate = cascades_detected / max(n_cascade_tasks, 1)
        gate = SLA_GATES["chain"]
        passed = detection_rate >= gate["min_cascade_detection"]
        return BenchmarkRun(
            suite="chain", n_items=sum(len(t.steps) for t in tasks),
            metrics={
                "cascade_detection_rate": round(detection_rate, 3),
                "n_cascade_tasks": n_cascade_tasks,
                "n_detected": cascades_detected,
            },
            samples=samples, passed=passed,
        )

    # ------------------------------------------------------------------
    # Suite 5: Chaos — deadlines + oracle outages + circuit breaker
    # ------------------------------------------------------------------

    async def _run_chaos(self) -> BenchmarkRun:
        # Build a registry where the only oracle has a tiny deadline budget.
        # Some requests will time out — engine must degrade gracefully.
        items = self._sliced(TRUTHFUL_QA[:5])
        engine = self._build_engine(items)

        graceful = 0
        crashed = 0
        samples: list[dict] = []
        for i, item in enumerate(items):
            # Set an absurdly tight deadline on half the requests
            tight = (i % 2 == 0)
            req = DeepVerificationRequest(
                prompt=item.prompt,
                response_text=item.response,
                tenant_id="bench",
                request_id=f"chaos-{i}",
                model_id="bench-model",
                tau=0.30, tau_prime=0.05,
                oracle_selection=OracleSelection(
                    priority_order=["customer_kb"],
                    deadline_ms=1 if tight else 300,
                ),
            )
            try:
                res = await engine.verify(req)
                # Graceful = produces a cert (any status) and doesn't raise
                graceful += 1
                samples.append({
                    "tight_deadline": tight,
                    "cert_status": res.payload.overall_status.value,
                    "degradations": res.payload.degradations,
                })
            except Exception as e:
                crashed += 1
                samples.append({
                    "tight_deadline": tight,
                    "exception": type(e).__name__,
                })

        rate = graceful / max(len(items), 1)
        gate = SLA_GATES["chaos"]
        passed = rate >= gate["min_graceful_degradation_rate"]
        return BenchmarkRun(
            suite="chaos", n_items=len(items),
            metrics={
                "graceful_degradation_rate": round(rate, 3),
                "n_graceful": graceful,
                "n_crashed": crashed,
            },
            samples=samples, passed=passed,
        )

    # ------------------------------------------------------------------
    # Engine assembly helpers
    # ------------------------------------------------------------------

    def _build_engine(
        self,
        items: list[BenchItem],
        steering_alpha: float = 1.5,
        force_engage: bool = False,
    ) -> DeepVerifierEngine:
        kb = LexicalKBIndex()
        seen: set[str] = set()
        for item in items:
            for doc_id, text in item.kb_documents:
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                kb.add(KBDocument(doc_id=doc_id, text=text, source_uri=f"kb://{doc_id}"),
                       tenant_id="bench")
        wrapped = NegationAwareOracle(
            inner=CustomerKBOracle(kb), name="customer_kb",
        )
        registry = OracleRegistry([wrapped])
        base = VerifierEngine(registry)
        ssh = StubSSHAdapter()
        steering = StubSteeringAdapter(SteeringConfig(alpha=steering_alpha))
        engine = DeepVerifierEngine(base=base, ssh=ssh, steering=steering)

        if force_engage:
            # Fake an alarm-driven engagement so capability cost is exercised
            steering.engage(scale=1.0, rho=0.99, step=0)
        return engine

    def _build_engine_from_chain(self, task: ChainTask) -> DeepVerifierEngine:
        kb = LexicalKBIndex()
        for doc_id, text in task.kb_documents:
            kb.add(KBDocument(doc_id=doc_id, text=text, source_uri=f"kb://{doc_id}"),
                   tenant_id="bench")
        wrapped = NegationAwareOracle(
            inner=CustomerKBOracle(kb), name="customer_kb",
        )
        registry = OracleRegistry([wrapped])
        base = VerifierEngine(registry)
        return DeepVerifierEngine(
            base=base,
            ssh=StubSSHAdapter(),
            steering=StubSteeringAdapter(),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sliced(self, xs: list[BenchItem]) -> list[BenchItem]:
        if self._sample_limit is None:
            return list(xs)
        return list(xs)[: self._sample_limit]

    @staticmethod
    def _classification_metrics(
        tp: int, fp: int, fn: int, tn: int, latencies_ms: list[float]
    ) -> dict:
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        accuracy = (tp + tn) / max(tp + fp + fn + tn, 1)
        sorted_lat = sorted(latencies_ms)
        def pct(p):
            if not sorted_lat:
                return 0.0
            idx = max(0, min(len(sorted_lat) - 1, int(round(p * (len(sorted_lat) - 1)))))
            return sorted_lat[idx]
        return {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "accuracy": round(accuracy, 3),
            "latency_mean_ms": round(statistics.fmean(latencies_ms) if latencies_ms else 0.0, 2),
            "latency_p50_ms": round(pct(0.50), 2),
            "latency_p95_ms": round(pct(0.95), 2),
            "latency_p99_ms": round(pct(0.99), 2),
        }


# ---------------------------------------------------------------------------
# Scorecard signing — wraps the scorecard in a TrustLens certificate so it is
# verifiable with the same `trustlens verify` CLI auditors already use.
# ---------------------------------------------------------------------------

def sign_scorecard(scorecard: Scorecard, signer: KeyPair) -> Scorecard:
    """Sign the scorecard. The signature covers the canonical JSON of the
    scorecard's content (everything except `signature`, `signer_key_id`,
    and `scorecard_id` itself).
    """
    import base64
    body = {
        "trustlens_version": scorecard.trustlens_version,
        "pipeline_version": scorecard.pipeline_version,
        "cert_schema_version": scorecard.cert_schema_version,
        "issued_at": scorecard.issued_at,
        "overall_passed": scorecard.overall_passed,
        "aggregate": scorecard.aggregate,
        "runs": [asdict(r) for r in scorecard.runs],
    }
    raw = canonical_json_for_dict(body)
    digest = hashlib.sha256(raw).hexdigest()
    sig_bytes = signer.private_key.sign(digest.encode("utf-8"))
    scorecard.scorecard_id = digest
    scorecard.signer_key_id = signer.key_id
    scorecard.signature = base64.b64encode(sig_bytes).decode("ascii")
    return scorecard


def verify_scorecard(scorecard: Scorecard, public_key) -> bool:
    """Offline-verify a signed scorecard with an Ed25519 public key."""
    import base64
    if not (scorecard.signature and scorecard.scorecard_id):
        return False
    body = {
        "trustlens_version": scorecard.trustlens_version,
        "pipeline_version": scorecard.pipeline_version,
        "cert_schema_version": scorecard.cert_schema_version,
        "issued_at": scorecard.issued_at,
        "overall_passed": scorecard.overall_passed,
        "aggregate": scorecard.aggregate,
        "runs": [asdict(r) for r in scorecard.runs],
    }
    raw = canonical_json_for_dict(body)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != scorecard.scorecard_id:
        return False
    try:
        public_key.verify(
            base64.b64decode(scorecard.signature),
            digest.encode("utf-8"),
        )
        return True
    except Exception:
        return False


def canonical_json_for_dict(d: dict) -> bytes:
    """Stable serialization of a plain dict — matches certificate canonical."""
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
