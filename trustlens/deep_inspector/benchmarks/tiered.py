"""TieredBenchmarkSuite — claim-aware scoring + tier-aware SLA gates.

A v2 of the original `BenchmarkSuite` that:
    1. Uses `score_payload` / `block_decision` from `scoring.py` instead of
       coarse `cert_status in (VERIFIED, PARTIAL)`.
    2. Reads SLA gates from `sla.py` according to the requested tier.
    3. Replaces the degenerate Pareto sweep — instead of toggling a stub
       steerer that doesn't mutate outputs, it sweeps an actual verifier
       knob (effective tau) so the capability/skepticism trade-off is real.
    4. Improves the chain suite's parent linkage so cascade detection
       triggers when an upstream turn is rejected.

Same Scorecard format as the original — signed and offline-verifiable
with `verify_scorecard` from `harness.py`.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from trustlens.deep_inspector.agentic_chain import TrustChain
from trustlens.deep_inspector.benchmarks.datasets import (
    CHAIN_TASKS,
    HALU_EVAL,
    PARETO_PROMPTS,
    TRUTHFUL_QA,
    BenchItem,
    ChainTask,
)
from trustlens.deep_inspector.benchmarks.harness import (
    BenchmarkRun,
    Scorecard,
)
from trustlens.deep_inspector.benchmarks.scoring import (
    block_decision,
    score_payload,
)
from trustlens.deep_inspector.benchmarks.sla import VerifierTier, gates_for
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
from trustlens.verifier.engine import VerificationRequest, VerifierEngine
from trustlens.version import (
    CERT_SCHEMA_VERSION,
    PIPELINE_VERSION,
    __version__,
)


class TieredBenchmarkSuite:
    """Tier-aware Deep Inspector benchmark."""

    def __init__(
        self,
        tier: VerifierTier = VerifierTier.LEXICAL,
        sample_limit_per_suite: Optional[int] = None,
        suite_filter: Optional[set[str]] = None,
    ):
        self.tier = tier
        self._sample_limit = sample_limit_per_suite
        self._suite_filter = suite_filter
        self._gates = gates_for(tier)

    async def run_all(self) -> Scorecard:
        runs: list[BenchmarkRun] = []
        for name, fn in [
            ("truthful_qa", self._truthful_qa),
            ("halu_eval", self._halu_eval),
            ("pareto", self._pareto),
            ("chain", self._chain),
            ("chaos", self._chaos),
        ]:
            if self._suite_filter and name not in self._suite_filter:
                continue
            t0 = time.perf_counter()
            run = await fn()
            run.elapsed_s = round(time.perf_counter() - t0, 3)
            runs.append(run)

        overall = all(r.passed for r in runs)
        agg = {
            "tier": self.tier.value,
            "n_suites": len(runs),
            "n_passed": sum(1 for r in runs if r.passed),
            "total_items": sum(r.n_items for r in runs),
            "total_elapsed_s": round(sum(r.elapsed_s for r in runs), 3),
        }
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
    # Suite 1: TruthfulQA — claim-aware precision/recall on factual claims
    # ------------------------------------------------------------------

    async def _truthful_qa(self) -> BenchmarkRun:
        items = self._sliced(TRUTHFUL_QA)
        engine = self._build_engine(items)
        gate = self._gates["truthful_qa"]

        latencies: list[float] = []
        tp = fp = fn = tn = 0
        samples: list[dict] = []

        for i, item in enumerate(items):
            req = self._req(item, f"tqa-{i}", tau=0.30, tau_prime=0.05)
            t0 = time.perf_counter()
            res = await engine.verify(req)
            latencies.append((time.perf_counter() - t0) * 1000.0)

            verdict = score_payload(res.payload)
            actually_supported = item.label == "supported"
            predicted = verdict.predicted_supported

            if actually_supported and predicted: tp += 1
            elif actually_supported and not predicted: fn += 1
            elif (not actually_supported) and (not predicted): tn += 1
            else: fp += 1

            samples.append({
                "prompt": item.prompt[:60],
                "label": item.label,
                "predicted_supported": predicted,
                "cert_status": verdict.cert_status,
                "n_claims": verdict.n_claims,
                "n_renderable": verdict.n_renderable,
                "n_bad": verdict.n_unsupported_or_contradicted,
            })

        metrics = self._classification_metrics(tp, fp, fn, tn, latencies)
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
    # Suite 2: HaluEval — block adversarial hallucinations, don't false-block
    # ------------------------------------------------------------------

    async def _halu_eval(self) -> BenchmarkRun:
        items = self._sliced(HALU_EVAL)
        engine = self._build_engine(items)
        gate = self._gates["halu_eval"]

        n_halluc = sum(1 for i in items if i.label == "hallucinated")
        n_supported = len(items) - n_halluc
        blocked_halluc = 0
        false_blocks = 0
        samples: list[dict] = []

        for i, item in enumerate(items):
            req = self._req(item, f"hal-{i}", tau=0.40, tau_prime=0.10)
            res = await engine.verify(req)
            blocked = block_decision(res.payload)

            if item.label == "hallucinated" and blocked: blocked_halluc += 1
            if item.label == "supported" and blocked: false_blocks += 1
            samples.append({
                "prompt": item.prompt[:60],
                "label": item.label,
                "blocked": blocked,
                "cert_status": res.payload.overall_status.value,
            })

        block_rate = blocked_halluc / max(n_halluc, 1)
        false_block_rate = false_blocks / max(n_supported, 1)
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
    # Suite 3: Pareto — REAL knob (effective tau) instead of stub steering
    # ------------------------------------------------------------------

    async def _pareto(self) -> BenchmarkRun:
        items = self._sliced(PARETO_PROMPTS)
        gate = self._gates["pareto"]
        # alpha → effective tau scaling. alpha=0 means baseline tau,
        # alpha=5 means tau is 1.5x baseline (more skeptical → fewer pass).
        baseline_tau = 0.30
        alphas = [0.0, 1.0, 2.5, 5.0]

        per_alpha: list[dict] = []
        capabilities: list[float] = []

        for alpha in alphas:
            engine = self._build_engine(items)
            tau = baseline_tau * (1.0 + alpha * 0.10)
            n_correct = 0
            for i, item in enumerate(items):
                req = self._req(item, f"par-{alpha}-{i}", tau=tau, tau_prime=0.05)
                res = await engine.verify(req)
                verdict = score_payload(res.payload)
                # Capability proxy: did supported items pass through unblocked?
                if verdict.predicted_supported and item.label == "supported":
                    n_correct += 1
            cap = n_correct / max(len(items), 1)
            capabilities.append(cap)
            per_alpha.append({
                "alpha": alpha,
                "effective_tau": round(tau, 3),
                "capability": round(cap, 3),
                "n_correct": n_correct,
                "n_total": len(items),
            })

        cap_drop_per_alpha = (capabilities[0] - capabilities[-1]) / (alphas[-1] or 1)
        # Curvature = how non-linear the capability/alpha curve is. A flat or
        # straight curve gets ~0; a curve that holds capability then collapses
        # gets a higher value. Useful to confirm a real Pareto exists.
        if len(capabilities) >= 3:
            mid = capabilities[len(capabilities) // 2]
            line = (capabilities[0] + capabilities[-1]) / 2
            curvature = abs(mid - line)
        else:
            curvature = 0.0

        cap_at_alpha_1 = next(
            (p["capability"] for p in per_alpha if p["alpha"] == 1.0), 0
        )
        passed = (
            cap_at_alpha_1 >= gate["min_capability_at_alpha_1"]
            and cap_drop_per_alpha <= gate["max_capability_drop_per_alpha"]
            and curvature >= gate["min_pareto_curvature"]
        )
        return BenchmarkRun(
            suite="pareto", n_items=len(items) * len(alphas),
            metrics={
                "alphas": alphas,
                "capability_per_alpha": capabilities,
                "capability_drop_per_alpha": round(cap_drop_per_alpha, 4),
                "pareto_curvature": round(curvature, 4),
            },
            samples=per_alpha, passed=passed,
        )

    # ------------------------------------------------------------------
    # Suite 4: Chain — cascade detection across multi-turn tasks
    # ------------------------------------------------------------------

    async def _chain(self) -> BenchmarkRun:
        tasks = list(CHAIN_TASKS)
        if self._sample_limit is not None:
            tasks = tasks[: self._sample_limit]
        gate = self._gates["chain"]

        cascades_detected = 0
        n_cascade_tasks = 0
        samples: list[dict] = []

        for task in tasks:
            engine = self._build_engine_from_chain(task)
            chain = TrustChain()
            prev_claim_ids: list[str] = []
            per_turn_blocked: list[bool] = []
            for step_idx, step in enumerate(task.steps):
                # Each new claim depends on every claim from the previous turn.
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
                    parent_claims=None,
                )
                res = await engine.verify(req)
                cur_claim_ids = [c.claim_id for c in res.payload.claims]
                if prev_claim_ids:
                    chain.add_turn(
                        turn_idx=step_idx,
                        claim_ids=cur_claim_ids,
                        parents={cid: prev_claim_ids for cid in cur_claim_ids},
                    )
                per_turn_blocked.append(block_decision(res.payload))
                prev_claim_ids = cur_claim_ids

            cascade = chain.cascade_summary()
            has_cascade = any(s.label == "hallucinated" for s in task.steps)
            # Detection signal: either the chain summary flagged an unreliable
            # turn, or any turn was blocked by the verifier.
            detected = (
                cascade["first_unreliable_turn"] is not None
                or any(per_turn_blocked)
            )
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
                "per_turn_blocked": per_turn_blocked,
            })

        rate = cascades_detected / max(n_cascade_tasks, 1)
        passed = rate >= gate["min_cascade_detection"]
        return BenchmarkRun(
            suite="chain", n_items=sum(len(t.steps) for t in tasks),
            metrics={
                "cascade_detection_rate": round(rate, 3),
                "n_cascade_tasks": n_cascade_tasks,
                "n_detected": cascades_detected,
            },
            samples=samples, passed=passed,
        )

    # ------------------------------------------------------------------
    # Suite 5: Chaos — tight deadlines + degraded oracles
    # ------------------------------------------------------------------

    async def _chaos(self) -> BenchmarkRun:
        items = self._sliced(TRUTHFUL_QA[:5])
        engine = self._build_engine(items)
        gate = self._gates["chaos"]

        graceful = 0
        crashed = 0
        samples: list[dict] = []
        for i, item in enumerate(items):
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
                graceful += 1
                samples.append({
                    "tight_deadline": tight,
                    "cert_status": res.payload.overall_status.value,
                    "degradations": res.payload.degradations,
                })
            except Exception as e:
                crashed += 1
                samples.append({"tight_deadline": tight, "exception": type(e).__name__})

        rate = graceful / max(len(items), 1)
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
    # Engine assembly + helpers
    # ------------------------------------------------------------------

    def _build_engine(self, items: list[BenchItem]) -> DeepVerifierEngine:
        kb = LexicalKBIndex()
        seen: set[str] = set()
        for item in items:
            for doc_id, text in item.kb_documents:
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                kb.add(KBDocument(doc_id=doc_id, text=text,
                                   source_uri=f"kb://{doc_id}"),
                       tenant_id="bench")
        wrapped = NegationAwareOracle(
            inner=CustomerKBOracle(kb), name="customer_kb",
        )
        registry = OracleRegistry([wrapped])
        base = VerifierEngine(registry)
        return DeepVerifierEngine(
            base=base,
            ssh=StubSSHAdapter(),
            steering=StubSteeringAdapter(SteeringConfig(alpha=1.5)),
        )

    def _build_engine_from_chain(self, task: ChainTask) -> DeepVerifierEngine:
        kb = LexicalKBIndex()
        for doc_id, text in task.kb_documents:
            kb.add(KBDocument(doc_id=doc_id, text=text,
                              source_uri=f"kb://{doc_id}"),
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

    def _req(
        self, item: BenchItem, request_id: str, tau: float, tau_prime: float
    ) -> DeepVerificationRequest:
        return DeepVerificationRequest(
            prompt=item.prompt,
            response_text=item.response,
            tenant_id="bench",
            request_id=request_id,
            model_id="bench-model",
            tau=tau, tau_prime=tau_prime,
            oracle_selection=OracleSelection(
                priority_order=["customer_kb"], deadline_ms=300,
            ),
        )

    def _sliced(self, xs: list[BenchItem]) -> list[BenchItem]:
        if self._sample_limit is None:
            return list(xs)
        return list(xs)[: self._sample_limit]

    @staticmethod
    def _classification_metrics(
        tp: int, fp: int, fn: int, tn: int, latencies: list[float]
    ) -> dict:
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        accuracy = (tp + tn) / max(tp + fp + fn + tn, 1)
        sorted_lat = sorted(latencies)
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
            "latency_mean_ms": round(statistics.fmean(latencies) if latencies else 0.0, 2),
            "latency_p50_ms": round(pct(0.50), 2),
            "latency_p95_ms": round(pct(0.95), 2),
            "latency_p99_ms": round(pct(0.99), 2),
        }
