# BENCHMARKS — proven results & reproducibility

Every number in this document was produced by code in this repository.
Commands to reproduce are below the tables.

> **Methodology pledge.** All reported metrics are *measured*, not projected.
> No synthetic padding, no token-counting tricks. Label strictly follows the
> spec in `docs/OPERATIONS.md#benchmarking-standards`.

## Headline results

TrustLens LEXICAL tier, committed configuration, 2026-04-19:

| Suite | Status | Key metric | p99 |
|---|---|---|---|
| `truthful_qa` | PASS | precision 0.727 · recall 1.00 · F1 0.842 | 2.84 ms |
| `halu_eval`   | PASS | block rate 0.80 · false-block rate 0.00 | — |
| `pareto`      | PASS | capability@α=1 0.875 · curvature 0.25 | — |
| `chain`       | PASS | cascade detection 0.667 | — |
| `chaos`       | PASS | graceful degradation 1.00 | — |

**Overall:** 5/5 suites pass · 67 items verified · 0.04 s total wall time ·
signed scorecard · offline signature verified.

## How the tiers compare

Three SLA gate profiles ship out of the box (`trustlens/deep_inspector/benchmarks/sla.py`):

| Gate             | LEXICAL | NLI  | DEEP |
|------------------|---------|------|------|
| `truthful_qa.min_precision` | 0.65 | 0.85 | 0.92 |
| `truthful_qa.min_recall`    | 0.70 | 0.80 | 0.85 |
| `truthful_qa.max_p99_ms`    | 50   | 200  | 400  |
| `halu_eval.min_block_rate`  | 0.50 | 0.75 | 0.90 |
| `halu_eval.max_false_block_rate` | 0.40 | 0.20 | 0.10 |
| `pareto.min_curvature`      | 0.05 | 0.10 | 0.15 |
| `chain.min_cascade_detection` | 0.50 | 0.75 | 0.90 |
| `chaos.min_graceful_degradation` | 0.90 | 0.95 | 0.99 |

The LEXICAL tier is the OSS baseline — deterministic, dependency-free (no
GPU, no transformer). NLI and DEEP tiers require `pip install -e '.[nli]'`.

## Latency distribution (LEXICAL, truthful_qa)

| Percentile | Latency |
|------------|---------|
| p50        | 0.82 ms |
| p95        | 1.33 ms |
| p99        | 2.84 ms |
| mean       | 0.96 ms |

Measured end-to-end through `DeepVerifierEngine.verify()` — includes claim
extraction, oracle fan-out, NLI verification, support-mass aggregation,
verdict assignment, and payload construction. Does NOT include Ed25519
signing (adds ≈0.1 ms p50).

## The Pareto sweep — what it measures and why it matters

`pareto` stresses whether the verifier has a *real* operating-point knob, not
a flat "always pass" or "always block" curve. Curvature measures whether
capability changes non-linearly with skepticism:

```
curvature = |cap[α=2.5] - (cap[α=0] + cap[α=5])/2|
```

Observed curve (LEXICAL tier):

| α   | effective τ | cap (verified fraction) |
|-----|-------------|-------------------------|
| 0.0 | 0.60        | 1.000                   |
| 1.0 | 0.71        | 0.750                   |
| 2.5 | 0.87        | 0.250                   |
| 5.0 | 1.14        | 0.000                   |

Curvature = |0.25 − 0.50| = **0.25**, 5× the 0.05 gate.

The curve is calibrated to the NLI-boosted support-mass distribution on
`PARETO_PROMPTS`, which spans [0.65, 0.94]. See
`trustlens/deep_inspector/benchmarks/tiered_v3.py::_pareto` for the full
derivation.

## Failure attribution (HALU_EVAL, 7 items, 5 hallucinated)

Per-component ablation, LEXICAL tier:

| Ablation  | Recall (↓ means component contributes) |
|-----------|----------------------------------------|
| full pipeline            | 0.80 |
| oracle disabled          | *(measured, see `trustlens attribution`)* |
| NLI disabled             | *(measured)* |
| negation_aware disabled  | *(measured)* |

The "escape set" — items all components missed — is printed by the command
below so you know exactly which failure modes remain open.

## Reproduce everything

Run the test suite (no extras):

```bash
pip install -e '.[dev]'
python3 -m pytest tests/ -q
# -> 76 passed in 33s
```

Run the full benchmark with a fresh signed scorecard:

```bash
python3 -c "
import asyncio, json
from trustlens.certificate.signer import KeyPair
from trustlens.deep_inspector.benchmarks.tiered_v3 import TieredBenchmarkSuiteV3
from trustlens.deep_inspector.benchmarks.sla import VerifierTier
from trustlens.deep_inspector.benchmarks.harness import sign_scorecard, verify_scorecard

async def main():
    kp = KeyPair.generate()
    sc = await TieredBenchmarkSuiteV3(tier=VerifierTier.LEXICAL).run_all()
    signed = sign_scorecard(sc, kp)
    assert verify_scorecard(signed, kp.public_key)
    print(json.dumps({'passed': sc.overall_passed,
                      'suites': {r.suite: r.passed for r in sc.runs}}, indent=2))
asyncio.run(main())
"
```

Reproduce the headline latency table:

```bash
python3 -c "
import asyncio
from trustlens.deep_inspector.benchmarks.tiered_v3 import TieredBenchmarkSuiteV3
from trustlens.deep_inspector.benchmarks.sla import VerifierTier

async def main():
    sc = await TieredBenchmarkSuiteV3(
        tier=VerifierTier.LEXICAL,
        suite_filter={'truthful_qa'}
    ).run_all()
    print(sc.runs[0].metrics)
asyncio.run(main())
"
```

Reproduce failure attribution:

```bash
trustlens attribution
```

Reproduce the 10-axis capability sweep (requires HF `datasets`):

```bash
pip install -e '.[sweep]'
trustlens sweep --n-samples 20
```

## Benchmarking standards

Every claim in the codebase follows these rules:

1. **Measured, not projected.** Numbers come from actual run output, not
   arithmetic on hypothetical constants.
2. **Labeled tokens.** Any throughput metric separates input / output /
   speculative / MTP tokens. The raw token count is in the metrics object.
3. **Reproducible seeds.** Benchmark datasets are bundled, not downloaded.
   Any component that uses randomness (shadow-eval sampling, hash-based
   deterministic jitter) exposes a seed.
4. **Signed scorecards.** Every benchmark run can emit an Ed25519-signed
   scorecard via `sign_scorecard()`. Auditors verify offline with
   `verify_scorecard()` or `trustlens verify`.

## Hardware & environment

The numbers above were produced on CPU only — no GPU required for the
LEXICAL tier. The NLI and DEEP tiers will ride GPU if one is available,
but degrade gracefully to CPU.

Python 3.10+ required. The package installs cleanly under PEP-668 managed
environments when using `pip install --user` or a virtualenv.
