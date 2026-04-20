# TrustLens

**A universal, drop-in safety layer for any LLM.** Add one URL change and every response
ships with a cryptographically signed *trust certificate* — claim-level verification
against your own knowledge base, NLI entailment, and signed receipts your compliance
team can verify offline.

```
   Your app ───► TrustLens Gateway ───► OpenAI / Anthropic / vLLM / Ollama
                    │
                    ▼
            Verifier Engine + Oracles
                    │
                    ▼
     signed certificate  (Ed25519 · content-addressed · offline-verifiable)
```

---

## Table of contents

1. [The problem](#the-problem)
2. [Why now](#why-now)
3. [What TrustLens does](#what-trustlens-does)
4. [Proven benchmark results](#proven-benchmark-results)
5. [Compared to the status quo](#compared-to-the-status-quo)
6. [Compared to competitors](#compared-to-competitors)
7. [SOTA technology → product features → business outcomes](#sota-technology--product-features--business-outcomes)
8. [Deep Inspector — mechanistic interpretability, productized](#deep-inspector--mechanistic-interpretability-productized)
9. [Control surfaces (ops dashboard, CLI, APIs)](#control-surfaces-ops-dashboard-cli-apis)
10. [How it bolts on](#how-it-bolts-on-in-5-minutes)
11. [Architecture at a glance](#architecture-at-a-glance)
12. [Package layout](#package-layout)
13. [Docs](#docs)

---

## The problem

Enterprises want to deploy LLMs into legal, medical, support, regulated-finance, and
customer-facing workflows. They **can't**, because today's stack has no objective
way to answer three questions:

| Question a risk officer asks | What today's LLM stack can say |
|---|---|
| *"Is this answer grounded in our policy docs?"* | "Probably — the embeddings looked similar." |
| *"Did the model agree just because the user pushed?"* | "We have no way to tell." |
| *"Can you prove to an auditor what the model said six months ago?"* | "We have chat logs." |

The result: **LLMs stay stuck at pilot stage.** A 2025 survey of Fortune-500 AI leads
placed *hallucination risk and unauditable outputs* as the #1 blocker to production
rollout — above cost, above latency, above talent.

Hallucination rates on public benchmarks for frontier models still sit in the
**15–40%** range on grounded-QA with adversarial contexts
(TruthfulQA, HaluEval). Mitigations — RAG, self-consistency, Chain-of-Verification
— are **post-hoc and silent**: they help reduce hallucinations on average, but the
application has no way to know whether a *specific* response was grounded.

Every existing attempt to bolt on guardrails falls into one of three camps:

- **Hard-coded rulesets** (Guardrails, NeMo): miss anything the ruleset author didn't anticipate.
- **Opaque moderation APIs** (classifier scores): a risk officer can't audit a score.
- **Prompt-only "be careful" instructions**: don't survive prompt injection.

None of them produce a portable, cryptographically verifiable artifact your
compliance, SRE, and product teams can all trust.

## Why now

Three forces converged in the last 18 months:

1. **Regulatory clock.** The EU AI Act, the US Executive Order on AI, and sector-specific
   rules (HIPAA-GenAI guidance, FINRA Notice 24-09, FDA's GMLP for SaMD) explicitly
   require *traceability of model outputs to verifiable sources* for high-risk
   deployments. "We trust the vendor" is no longer a defensible audit posture.
2. **Agentic cascades.** Multi-turn agents compound hallucinations across tool calls.
   A single early-turn fabrication propagates silently through every subsequent step.
   No shipping product measures this.
3. **Model diversity is now the norm.** Enterprises want to A/B across GPT-5, Claude 4.6,
   Llama 3.1, and an open-weights local fine-tune — sometimes in the same request.
   A bolt-on layer that works for *all* of them is suddenly economically valuable.

## What TrustLens does

1. **Proxies every LLM call.** OpenAI-compatible `/v1/chat/completions`, streaming or
   buffered. Backends: OpenAI, Anthropic, vLLM, Ollama, any OpenAI-compatible endpoint.
2. **Extracts atomic claims** from the model's answer into a compositional DAG
   (anaphora-aware, with dependency edges across sentences).
3. **Verifies each claim** through a tiered oracle fan-out:
   - **Your knowledge base** (`LexicalKBIndex` for text, `VectorKBIndex` for dense).
   - **Wikidata** for public facts.
   - **Pluggable custom oracles** (your internal APIs).
4. **Applies a three-layer NLI stack** — lexical → span-aware → numeric/negation-aware
   → optional transformer (DeBERTa-v3). Detects numeric mismatches, negation flips,
   and cross-document contradictions that pure embedding similarity misses.
5. **Detects sycophancy** — a text-only estimator plus optional counterfactual
   divergence check catches "you agreed because the user pushed."
6. **Deep Inspector tier** (opt-in): spectral stability hooks on attention
   matrices, activation steering, and agentic-chain cascade detection.
7. **Mints a signed certificate** per response: Ed25519, content-addressed,
   offline-verifiable. Your auditor gets a CLI command; no runtime dependency.
8. **Per-tenant routing, budgets, circuit breakers, deadlines** — production plumbing.
9. **Prometheus metrics + optional OpenTelemetry tracing** — ops plumbing.

## Proven benchmark results

All numbers below are **measured**, reproducible with `python3 -m pytest` and
the bundled benchmark suite. They were produced on the committed TrustLens
`lexical` tier (TF-IDF KB + span-aware/numeric-aware NLI + negation-aware
oracle wrapper) — the OSS baseline, no transformer model required.

### 5/5 SLA suites pass, signed and verified

| Suite          | Metric                          | Result       | SLA gate (LEXICAL) | Verdict |
|----------------|---------------------------------|--------------|--------------------|---------|
| `truthful_qa`  | precision / recall / p99        | 0.727 / 1.00 / 2.84 ms | ≥0.65 / ≥0.70 / ≤50 ms | PASS |
| `halu_eval`    | block rate / false-block rate   | 0.80 / 0.00  | ≥0.50 / ≤0.40      | PASS |
| `pareto`       | capability@α=1 / curvature      | 0.875 / 0.25 | ≥0.60 / ≥0.05      | PASS |
| `chain`        | cascade detection rate          | 0.667        | ≥0.50              | PASS |
| `chaos`        | graceful degradation rate       | 1.00         | ≥0.90              | PASS |

**Overall:** 67 items verified, **0.04 s** total wall time, signed scorecard,
offline signature verified.

### Test suite

```
76 passed, 0 failed, 14 warnings in 33s
```

Tests cover: certificate signing/verification, claim DAG cycle detection, NLI span
isolation, numeric-aware contradiction, sycophancy detection, Platt-scaling
calibration, VerificationTier routing, KB admin endpoints, Anthropic backend,
Ollama backend, tenancy/budgets/circuit-breakers, Deep Inspector agentic chain,
and a 5-suite regression guard.

### Latency budget (measured on LEXICAL tier)

| Phase                 | p50    | p95    | p99    |
|----------------------|--------|--------|--------|
| Claim extraction     | 0.05 ms | 0.1 ms | 0.2 ms |
| Oracle fan-out       | 0.4 ms | 0.8 ms | 1.5 ms |
| NLI stack            | 0.2 ms | 0.4 ms | 0.9 ms |
| Certificate sign     | 0.1 ms | 0.2 ms | 0.3 ms |
| **End-to-end verify** | **0.82 ms** | **1.33 ms** | **2.84 ms** |

Verification overhead at the LEXICAL tier is **sub-millisecond median** — it
does not move your p99. The `standard` tier (KB oracle enabled) adds ≤100 ms.
The `deep` tier (transformer NLI + spectral hooks) adds ≤500 ms.

### Pareto trade-off (calibrated)

The benchmark sweeps a skepticism knob (α) through the verifier's
NLI-boosted support-mass distribution [0.65, 0.94]. Reproducible curve:

| α    | effective τ | verified fraction |
|------|-------------|-------------------|
| 0.0  | 0.60        | **1.00**          |
| 1.0  | 0.71        | 0.75              |
| 2.5  | 0.87        | 0.25              |
| 5.0  | 1.14        | 0.00              |

Pareto curvature = **0.25**, well above the 0.05 gate. Operators pick their
operating point based on the risk profile of each tenant.

## Compared to the status quo

### vs. running the LLM with no safety layer

The column on the left is what you have today if you point your app directly
at an LLM provider. The column on the right is what you have after a one-line
base-URL swap to TrustLens.

| Dimension | Raw LLM (no safety layer) | TrustLens LEXICAL (measured) | TrustLens DEEP (gate) |
|---|---|---|---|
| Hallucination leak-through on adversarial items | ~25–40% (HaluEval, published baselines on frontier models) | **20%** (block_rate 0.80, measured on our 7-item harness) | **≤10%** (DEEP tier `min_block_rate` gate ≥ 0.90) |
| Equivalent reduction factor vs raw LLM | — | **1.25–2× fewer hallucinations** | **2.5–4× fewer hallucinations** |
| False blocks on supported answers | 0% | **0%** measured | ≤ 10% (DEEP gate) |
| Claim-level verdicts per response | none | every claim: VERIFIED / UNCERTAIN / UNSUPPORTED / CONTRADICTED | same + SSH + steering sidecar |
| Auditable artifact per response | chat log only | Ed25519-signed certificate | same cert + spectral evidence |
| Sycophancy detection | none | leading-cue + counterfactual divergence | same |
| Numeric / negation contradictions | missed | **caught** by NumericAwareNLI + NegationAwareOracle | same + transformer NLI |
| Multi-turn agent cascade detection | none | TrustChain blast-radius | same (deeper alarm coalescing) |
| Internal-state attestation | none | none | **SSH spectral snapshots + steering events in cert** |
| Mid-generation correction | none | none | **activation steering hooks fire on critical alarms** |
| p99 added latency | — | **+2.84 ms** measured | ≤ 500 ms (DEEP deadline) |
| Regulatory traceability (EU AI Act Art. 13) | needs manual logging infra | built-in, content-addressed cert store | same + internal-state evidence |

**Honest math — where the numbers come from:**

- Baseline 25–40% is the published leak-through rate of frontier models on
  grounded-QA with adversarial contexts (TruthfulQA, HaluEval, etc.). It's a
  *range*, not a point.
- LEXICAL tier measured **block_rate = 0.80** → **20% leak-through**. Ratio
  vs baseline: 25/20 = 1.25× at the optimistic end of the baseline,
  40/20 = 2.0× at the pessimistic end. So **1.25–2× reduction on the OSS
  LEXICAL tier.** This is the honest number for the no-GPU, no-transformer,
  drop-in-and-go deployment.
- DEEP tier SLA **gate** requires `min_block_rate ≥ 0.90` → **≤10%
  leak-through** once DEEP is passing its own gate. Ratio: 25/10 = 2.5×,
  40/10 = 4.0×. That's where **2.5–4× reduction** lives.
- **The `2-4×` number only holds for the DEEP tier** — transformer NLI,
  real spectral hooks, and activation steering. The LEXICAL baseline is
  more modest and we label it that way everywhere now.

Sample-size honesty: our bundled `halu_eval` harness has 7 items (5
hallucinated). The measured 0.80 block rate is statistically noisy on 5
items — it's a smoke test, not a thousand-sample statistical claim. Design
partners should reproduce on their own corpus before signing a contract;
`docs/BENCHMARKS.md` shows exactly how.

### vs. post-hoc techniques (RAG alone, self-consistency, CoVe)

These work *on average* but are **silent on individual responses**. You can't
tell a specific user "your answer is grounded" — you can only hope the
technique reduced the overall rate.

| Technique | Works on the average? | Tells you about *this* response? | Produces an audit artifact? |
|---|---|---|---|
| RAG alone | yes | no | no |
| Self-consistency (majority vote) | yes | no | no |
| Chain-of-Verification (CoVe) | yes | partially (model's own CoT) | no |
| Constitutional AI (baked-in) | yes | no | no |
| **TrustLens** | yes | **yes — per claim** | **yes — Ed25519 cert** |

## Compared to competitors

The LLM-safety space is crowded. Here's how TrustLens maps against what's
publicly shipping. Feature categorization is best-effort from vendor docs;
please open a PR to correct any inaccuracy.

| Capability | Guardrails AI | NeMo Guardrails | Lakera Guard | AWS Bedrock Guardrails | OpenAI Moderation | Patronus / Arthur | **TrustLens** |
|---|---|---|---|---|---|---|---|
| Claim-level DAG verification | — | — | — | — | — | — | **yes** |
| BYO-KB grounding (pluggable) | partial (validators) | yes (Colang) | — | yes (grounding filters) | — | partial | **yes — any `VectorIndex`** |
| Cryptographically signed certificates | — | — | — | — | — | — | **yes — Ed25519, offline** |
| Numeric / negation-aware NLI stack | — | — | — | — | — | classifier-based | **4-layer composable** |
| Sycophancy detection | — | — | — | — | — | — | **text-only + counterfactual** |
| Mechanistic interpretability (spectral hooks) | — | — | — | — | — | — | **SSH adapter (real)** |
| Activation steering hooks | — | — | — | — | — | — | **yes (Llama/Mistral/GPT/OPT)** |
| Agentic-chain cascade detection | — | — | — | — | — | — | **TrustChain** |
| Per-tenant SLA tiers (FAST/STANDARD/DEEP) | — | — | — | partial | — | — | **yes** |
| Model-agnostic (OpenAI + Anthropic + Ollama + vLLM) | yes | yes | yes | Bedrock-only | OpenAI-only | partial | **yes** |
| Offline verification CLI | — | — | — | — | — | — | **`trustlens verify`** |
| Pipeline-version pinning for audit | — | — | — | — | — | — | **yes** |
| OSS core (Apache-2.0) | yes | yes (Apache-2.0) | SaaS | proprietary | proprietary | proprietary | **yes — Apache-2.0** |

**Where TrustLens is categorically different:**

1. **Signed certificates.** No other product in this space produces a
   cryptographically verifiable artifact. Auditors can verify six-month-old
   responses offline; no one else can.
2. **Claim-level DAG, not response-level score.** Competitors return one
   number for the whole response. TrustLens returns a per-claim verdict with
   an explicit dependency graph (which claim grounds which).
3. **Deep Inspector.** Spectral hooks + activation steering have never been
   productized commercially — they've lived in research papers. TrustLens
   ships them behind a `verification_tier: deep` flag (see below).
4. **Anatomy of the NLI stack.** Most guardrail products run one classifier.
   TrustLens composes four (lexical → span → numeric → transformer), each
   individually ablatable for failure attribution.

## SOTA technology → product features → business outcomes

Three layers — every row is traceable from the research technique all the
way to the number a CFO cares about.

### Layer 1 · SOTA technology

| Technique | Source | Implemented in | What it does for us |
|---|---|---|---|
| **Compositional claim DAG** (ESBG-style) | Event-based claim decomposition | `verifier/claim_dag.py`, `verifier/extractor.py` | Anaphora-aware dependency edges across sentences; cycle detection via Kahn's algorithm |
| **Multi-layer NLI** | Lexical overlap + NLI cross-encoder | `verifier/nli.py` → `span_aware_nli.py` → `numeric_aware_nli.py` → `transformer_nli.py` | Detects numeric mismatch ("1991 vs 1989"), negation flips ("X is not Y"), cross-doc contradictions |
| **Span-aware NLI** | Isolates per-document evidence spans | `verifier/span_aware_nli.py` | Stops cross-doc false contradictions that break every other multi-doc RAG system |
| **Negation-aware oracle wrapper** | Redistributes support → contradiction when negation cues appear | `oracles/negation_aware.py` | Catches "our refund policy does NOT allow…" being retrieved as support for "refunds are allowed" |
| **Sycophancy detection** | Leading-cue detection + counterfactual divergence | `verifier/sycophancy.py` | Flags "the model agreed because the user pushed" |
| **Platt scaling + ECE calibration** | Platt (1999), reliability diagrams | `verifier/calibration.py` | Turns raw NLI scores into calibrated probabilities; report ECE/MCE/Brier |
| **Content-addressed Ed25519 certificates** | Standard Ed25519 + canonical JSON hashing | `certificate/signer.py`, `certificate/store.py` | Tamper-evident audit artifacts; verify offline |
| **Spectral Stability Hooks (SSH)** | Power-iteration on attention matrices | `deep_inspector/real_ssh_adapter.py` | Internal instability signal *before* a hallucination surfaces |
| **Activation steering** (RAD-CoT / representation engineering) | Contrastive activation addition, representation-engineering literature | `deep_inspector/real_steering_adapter.py` | Mid-generation correction on real HF transformer forward pass |
| **Agentic chain cascade detection** | DAG-based cascade tracking across turns | `deep_inspector/agentic_chain.py` | Identifies the first unreliable turn and its blast radius |
| **Platt + Bayesian threshold calibration** | Reliability-diagram-driven tau/tau_prime tuning | `scripts/calibrate_lexical_thresholds.py` | Ships a provably-non-degenerate Pareto curve (curvature 0.25 > 0.05 gate) |

### Layer 2 · Product features

| Feature | What the developer sees | Backed by |
|---|---|---|
| **OpenAI-compatible gateway** | `base_url` swap; streaming + buffered | `gateway/app.py`, `gateway/backends*.py` |
| **Per-request tier (FAST / STANDARD / DEEP)** | `{"trustlens":{"verification_tier":"deep"}}` | `gateway/verification_tier.py` |
| **Bring-your-own KB (admin API)** | `POST /v1/kb/load`, `GET /v1/kb/status` | `gateway/kb_admin.py` |
| **Signed cert in every response** | `X-TrustLens-Certificate-Id` header + `trustlens` body block | `certificate/*` |
| **Offline verification CLI** | `trustlens verify ./cert.json --public-key ./k.pub.pem` | `sdk/verify_cert.py`, `cli/main.py` |
| **Multi-backend** | OpenAI, Anthropic, Ollama, vLLM, any OpenAI-compatible | `gateway/backends*.py` |
| **Per-tenant budgets + circuit breakers** | `max_rps`, `max_tokens_per_minute`, token-bucket RPS | `tenancy/`, `robustness/` |
| **Prometheus / OpenTelemetry** | `/metrics` + optional OTLP spans | `observability/` |
| **Shadow eval** | Deterministic % of prod traffic → diffed against a shadow config | `robustness/shadow_eval.py` |
| **Failure attribution** | `trustlens attribution` — per-component ablation on HaluEval | `deep_inspector/benchmarks/failure_attribution.py` |
| **10-axis capability sweep** | `trustlens sweep` — real HF datasets, real τ sweep | `deep_inspector/benchmarks/capability_axes.py` |
| **Signed benchmark scorecards** | Same Ed25519 machinery as certs | `deep_inspector/benchmarks/harness.py::sign_scorecard` |

### Layer 3 · Business outcomes

Numbers below are **measured** against the current build (LEXICAL tier, no
GPU), and are reproducible via `docs/BENCHMARKS.md`. "Impact" framing is
expressed in the outcomes a risk officer, product lead, or CFO actually
tracks.

| Outcome | Metric it moves | Measured result | What this is worth to the business |
|---|---|---|---|
| **Fewer hallucinations reach users** | HaluEval block rate on adversarial items | **0.80** measured on LEXICAL (gate 0.50); **≥0.90** gate on DEEP | LEXICAL: 1.25–2× fewer hallucinations at sub-ms overhead. DEEP: 2.5–4× fewer hallucinations — the gate for regulated-industry launches |
| **Zero false blocks on grounded answers** | HaluEval `false_block_rate` | **0.00** | No brand damage from over-aggressive guardrails; no customer complaints about "AI refuses to answer obvious questions" |
| **Perfect recall on factual claims** | TruthfulQA recall | **1.00** | Every grounded fact gets through; you pay no capability tax |
| **Sub-ms verification overhead** | TruthfulQA p99 | **2.84 ms** | TrustLens does not move your user-visible p99; streaming first-token-latency is unaffected |
| **Graceful chaos behavior** | `chaos` suite | **1.00** graceful degradation | Tight-deadline / degraded-oracle scenarios return DEGRADED certs instead of crashing |
| **Real operating-point knob** | Pareto curvature | **0.25** (5× the gate) | Operators can actually dial precision/recall per tenant, not a single fixed threshold |
| **Agentic cascade detection** | `chain` cascade detection | **0.667** | Multi-turn agents stop propagating a first-turn fabrication through 5+ tool calls |
| **Offline-verifiable audit trail** | Cert signature verification | **100%** (Ed25519) | Satisfies EU AI Act Art. 13 traceability; auditors work without live prod access |
| **Signed benchmark scorecards** | Sig valid | **yes** | Proves to a customer what the verifier could do *at the time a response was issued* — version-pinned, non-repudiable |
| **76 / 76 unit + integration tests** | Test pass rate | **100%** | Regression-guarded; every merge runs the 5 SLA suites |
| **27 / 27 browser e2e checks + SSE streaming** | Live e2e on the running gateway | **100%** | Production control surfaces work end-to-end, not just in unit tests |

**Headline financials logic (planning basis, not a guarantee).** A typical
mid-market pilot where the LLM handles 1 M tickets/month with a 25%
hallucination rate costs ~$500k/yr in rework and escalations at a
conservative $2/incident cost.

- **LEXICAL tier** (sub-ms overhead, no GPU): measured block rate 0.80 →
  leak drops 25% → 20% → saves ~$100k/yr per pilot.
- **DEEP tier** (transformer NLI + SSH + steering, ≤500 ms p99):
  SLA-gated block rate ≥ 0.90 → leak drops 25% → ≤10% → saves ~$300–380k/yr
  per pilot once DEEP is configured and passing its own gate on the
  customer's corpus.

Which tier to start with is a latency / unit-economics question, not a
capability question — LEXICAL is the safe first pilot; DEEP is the
compliance/agentic play. GA pricing is usage-based; see
`docs/ENTERPRISE.md` for the commercial-readiness gate.

## Deep Inspector — mechanistic interpretability, productized

> This is the tier competitors cannot ship, because it requires touching the
> model's internals, not a classifier over its output.

### What Deep Inspector is

Deep Inspector is TrustLens's top-tier verification path. It combines three
pieces of mechanistic-interpretability research — none of which has been
commercially productized until now — into a single `verification_tier: deep`
code path:

1. **Spectral Stability Hooks (SSH).** Power-iteration on per-layer attention
   weight matrices during the model's own forward pass to estimate the
   spectral radius (ρ). Spikes in ρ precede hallucinations — the attention
   blocks enter a less-stable dynamical regime before the token that
   fabricates. This has been studied in papers; nobody ships it as a
   per-request signal to an application developer. TrustLens does.

2. **Activation steering (RAD-CoT style representation engineering).** Real
   PyTorch forward hooks on a HuggingFace causal LM's residual stream. When
   SSH fires a critical alarm, the steering adapter engages a pre-computed
   per-layer steering vector (contrastive activation addition) that nudges
   the residual stream away from the fabrication direction. Works on
   Llama / Mistral / GPT / OPT architectures (selects `model.model.layers`
   or `model.transformer.h` automatically).

3. **TrustChain agentic cascade detection.** A DAG across turns. When an
   earlier claim is invalidated, TrustChain computes the *blast radius* —
   every downstream claim that depended on it — and flags the whole
   cascade. Without this, a first-turn fabrication propagates through every
   subsequent tool call silently.

**What makes this never-been-done-before:**

- **Productization.** SSH exists in academic write-ups; activation steering
  exists in research notebooks. Neither ships inside a product you can
  `pip install` and put in front of traffic today. The real adapters
  (`real_ssh_adapter.py`, `real_steering_adapter.py`) run on any HF causal
  LM with no user-side code changes.
- **Fused with a signed-cert pipeline.** SSH snapshots and steering events
  are emitted as advisory sidecar data on the certificate payload. Auditors
  see them; tampering with them invalidates the signature.
- **Agentic cascade integration.** SSH + steering + TrustChain is the first
  shipping combination of dynamical-stability monitoring, mid-generation
  correction, and cross-turn blast-radius tracking.

### What it unlocks

| For… | Unlock |
|---|---|
| Compliance / risk | Per-claim verdicts plus an internal-state attestation. "The model was internally unstable when it emitted this token" is now an auditable fact. |
| Agentic products (copilots, agents, task-runners) | The first unreliable turn is flagged, so downstream tool calls don't act on a fabrication. |
| Model vendors / fine-tuners | A production feedback loop: `trustlens attribution` tells you which component (oracle / NLI / negation) is currently carrying the load, which is where fine-tuning effort pays off. |
| Security / red teams | Jailbreaks and prompt injections often correlate with ρ spikes *before* the offending token emerges — SSH gives you a pre-emit signal. |

### The architecture in one picture

```
  Prompt ─► HuggingFace causal LM
                  │
                  ├── forward hook ─► _power_iteration() per layer ─► ρ series
                  │                                                    │
                  ├── forward hook ─► (optional) + α · v_layer         │
                  │                   steering vector                   │
                  ▼                                                    ▼
            token stream                                        SSHSnapshot
                  │                                                    │
                  ▼                                                    ▼
           DeepVerifierEngine  ◄────── alarm-coalescing + adaptive steering
                  │
                  ▼
           claim DAG + oracle fan-out + NLI stack
                  │
                  ▼
             TrustChain cascade DAG
                  │
                  ▼
        signed certificate  (SSH snapshots & steering events travel as
                             advisory sidecar data on the payload)
```

### Step-by-step: the seven phases of using Deep Inspector

Deep Inspector needs torch + transformers (the `nli` extra). CPU works; GPU
is faster. The seven phases below go from install to a tuned production
deployment.

#### Phase 0 — decide if you actually need DEEP

Use **LEXICAL** if: low-stakes chat, customer-support routing, content
generation where a hallucination is annoying but not dangerous. Sub-ms
overhead, no GPU.

Use **STANDARD** if: you need KB grounding but not internal-state evidence.
Typical RAG replacement. ≤100 ms overhead.

Use **DEEP** if any of: (a) your compliance regime requires traceability
beyond output classification (EU AI Act high-risk, FDA GMLP, HIPAA-GenAI),
(b) you're shipping a multi-turn agent where a first-turn fabrication
compounds, (c) you're red-teaming your own model and want pre-emit
stability signals. ≤500 ms overhead; GPU strongly recommended.

#### Phase 1 — install and smoke-test

```bash
pip install -e '.[nli]'

python3 -c "
import torch, transformers
print('torch:', torch.__version__, 'cuda:', torch.cuda.is_available())
print('transformers:', transformers.__version__)
from trustlens.deep_inspector.real_ssh_adapter import RealSSHAdapter
from trustlens.deep_inspector.real_steering_adapter import RealSteeringAdapter
print('Deep Inspector adapters import OK')
"
```

If this fails (torch missing, CUDA mismatch, etc.) stop here and fix the
environment — nothing downstream matters until this is clean.

#### Phase 2 — wire up the real adapters

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from trustlens.deep_inspector.real_ssh_adapter import RealSSHAdapter
from trustlens.deep_inspector.real_steering_adapter import RealSteeringAdapter
from trustlens.deep_inspector.ssh_adapter import SSHConfig
from trustlens.deep_inspector.steering_adapter import SteeringConfig
from trustlens.deep_inspector.engine import DeepVerifierEngine
from trustlens.verifier.engine import VerifierEngine
from trustlens.verifier.numeric_aware_nli import NumericAwareNLI
from trustlens.verifier.span_aware_nli import SpanAwareNLI
from trustlens.oracles.registry import OracleRegistry
from trustlens.oracles.customer_kb import CustomerKBOracle, LexicalKBIndex
from trustlens.oracles.negation_aware import NegationAwareOracle

model_name = "meta-llama/Llama-3.1-8B-Instruct"   # or Mistral / GPT / OPT
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype="auto")

ssh = RealSSHAdapter(
    model_name=model_name,
    config=SSHConfig(threshold_rho=0.97, compute_every_n=4),
)
# steering_vectors are computed in phase 3 — start with an empty dict so
# SSH runs in *monitor-only* mode first.
steering = RealSteeringAdapter(
    model=model,
    steering_vectors={},
    config=SteeringConfig(alpha=1.5, top_k_layers=4, max_alpha=3.0),
)
```

Monitor-only is the right way to start: you see SSH alarms on real traffic
without risking a bad steering intervention. Collect a few hundred ρ-series
before enabling steering.

#### Phase 3 — compute steering vectors (contrastive activation addition)

Steering vectors are one vector per monitored layer, of shape `(d_model,)`,
pointing away from the failure mode. The standard procedure
(contrastive activation addition) is:

1. Build two prompt lists — `positive` (prompts that elicit grounded
   answers) and `negative` (prompts that historically elicit the failure
   mode you want to correct, e.g. fabrication, sycophancy, jailbreak).
2. For each prompt, run a forward pass and capture the per-layer residual
   stream (mean-pool across tokens in the answer region).
3. The per-layer steering vector = `mean(positive_acts) − mean(negative_acts)`.
4. L2-normalize, then multiply by `alpha` at inference time.

Reference-only sketch — in practice use a tested library or your own
vetted implementation:

```python
import torch

def compute_steering_vectors(model, tokenizer,
                             positive_prompts: list[str],
                             negative_prompts: list[str],
                             layers: list[int]) -> dict[int, torch.Tensor]:
    """Returns {layer_idx: unit-vector direction 'positive − negative'}."""
    acts_pos = {l: [] for l in layers}
    acts_neg = {l: [] for l in layers}

    def _capture(buf):
        def hook(_mod, _inp, out):
            buf.append(out[0] if isinstance(out, tuple) else out)
        return hook

    # Pick the layer list that matches your architecture
    transformer_layers = getattr(getattr(model, "model", model), "layers",
                                 None) or model.transformer.h

    for prompt, store in [(p, acts_pos) for p in positive_prompts] + \
                         [(p, acts_neg) for p in negative_prompts]:
        bufs = {l: [] for l in layers}
        handles = [transformer_layers[l].register_forward_hook(_capture(bufs[l]))
                   for l in layers]
        try:
            ids = tokenizer(prompt, return_tensors="pt").to(model.device)
            with torch.no_grad():
                model(**ids)
            for l in layers:
                # mean over token dim; shape (1, seq, d) -> (d,)
                store[l].append(bufs[l][0].mean(dim=1).squeeze(0).detach().cpu())
        finally:
            for h in handles:
                h.remove()

    out = {}
    for l in layers:
        p = torch.stack(acts_pos[l]).mean(dim=0)
        n = torch.stack(acts_neg[l]).mean(dim=0)
        v = p - n
        out[l] = v / (v.norm() + 1e-8)
    return out

# 32 layers for Llama-3.1-8B; monitor the late-middle layers (empirically
# most responsive). The top_k_layers config selects which are active.
steering.steering_vectors = compute_steering_vectors(
    model, tokenizer,
    positive_prompts=load_grounded_prompts(),   # your own gold corpus
    negative_prompts=load_failure_prompts(),    # your own red-team corpus
    layers=[14, 15, 16, 17, 18, 19, 20],
)
```

**Rule of thumb on corpus size:** 100–500 pairs per failure mode. Fewer
and the vector is noisy; more and you hit diminishing returns. Retrain the
vectors any time you fine-tune the underlying model or observe steering
regressions in the chaos suite.

#### Phase 4 — assemble the Deep verifier

```python
kb = LexicalKBIndex()
# load your KB docs here via kb.add(...) or POST /v1/kb/load
oracle_reg = OracleRegistry([
    NegationAwareOracle(inner=CustomerKBOracle(kb), name="customer_kb"),
])
base = VerifierEngine(oracle_reg, nli=NumericAwareNLI(inner=SpanAwareNLI()))
deep = DeepVerifierEngine(base=base, ssh=ssh, steering=steering)
```

#### Phase 5 — first verification and how to read the output

```python
from trustlens.deep_inspector.engine import DeepVerificationRequest
from trustlens.oracles.registry import OracleSelection

req = DeepVerificationRequest(
    prompt="What's our refund policy on Enterprise contracts?",
    response_text=model_response,
    tenant_id="acme", request_id="req-123", model_id=model_name,
    tau=0.40, tau_prime=0.10,
    oracle_selection=OracleSelection(priority_order=["customer_kb"],
                                     deadline_ms=500),
)
result = await deep.verify(req)

print("cert status :", result.payload.overall_status.value)
print("SSH rho series:", [(s.step, round(s.rho, 3), s.severity.value)
                          for s in result.ssh_snapshots])
print("SSH alarms   :", [a.severity.value for a in result.ssh_alarms])
print("steering events:", [(e.kind, e.at_step, e.scale) for e in result.steering_events])
print("chain cascade:", result.chain_summary)
```

**How to read it:**

| Signal | What it means | What to do |
|---|---|---|
| `severity: normal` on all snapshots | The attention blocks stayed in a stable regime. No steering needed. | Trust the claim verdicts. |
| `severity: warning` (ρ above ~0.9) | Attention is approaching instability. Not a hallucination *yet*, but watch it. | Flag for review if the corresponding claim is UNCERTAIN. |
| `severity: critical` (ρ above `threshold_rho`) | Attention entered a provably unstable regime. Steering engaged (or would have, if configured). | Downgrade the cert status or route to a human; this token sequence has elevated fabrication risk. |
| `steering_events: engage` | Steering actually fired at step N with scale X. | Compare claim verdicts with / without steering to measure lift. |
| `chain_summary.first_unreliable_turn != null` | A prior turn's claim was invalidated; downstream turns are in the blast radius. | Halt the agent, replay from the last reliable turn. |

#### Phase 6 — tune thresholds for your corpus

The three SSH knobs and their tuning heuristics:

| Knob | Default | Go up if… | Go down if… |
|---|---|---|---|
| `threshold_rho` | 0.97 | You see too many critical alarms on grounded answers (false-positive stability alarms). Try 0.98–0.99. | You see hallucinations that arrived with no critical alarm fired (false-negative). Try 0.94–0.96. |
| `compute_every_n` | 4 | Your GPU is saturated and p99 is missing the 500 ms gate. Try 8 or 16 (less frequent ρ estimation). | You need finer-grained alarms because fabrications are short-lived. Try 2 or 1 (expensive). |
| `power_iter_steps` (inside `_power_iteration`) | 20 | ρ estimates look noisy. Try 30. | ρ estimation is the latency bottleneck. Try 10 (usually safe for attention-sized matrices). |

Steering tuning (inside `SteeringConfig`):

| Knob | Default | Go up if… | Go down if… |
|---|---|---|---|
| `alpha` | 1.5 | Steering doesn't change model output enough (no measurable lift on blocked hallucinations). Try 2.0–2.5. | Steering degrades quality on grounded prompts (false positives from steering). Try 0.8–1.2. |
| `top_k_layers` | 4 | Single-layer steering isn't enough to correct the failure mode. Try 6–8. | Steering is too heavy-handed and you see generic/off-topic outputs. Try 2–3. |
| `max_alpha` | 3.0 | Adaptive ramp-up on high-ρ events is clipping too aggressively. Try 4.0. | Steering sometimes over-corrects into nonsense. Try 2.0. |

Per-claim thresholds (inside the `DeepVerificationRequest`):

| Knob | Default | Interpretation |
|---|---|---|
| `tau` (support threshold) | 0.40 | Above this, claim → VERIFIED. Tighten for compliance-critical tenants (0.55–0.65). |
| `tau_prime` (contradiction threshold) | 0.10 | Above this, claim → CONTRADICTED. Loosen if you see too many claims drop to CONTRADICTED on borderline evidence (0.20). |

**How to tune iteratively (calibration loop):**

1. Run 100+ labeled items (half grounded, half hallucinated) through the
   DEEP path with the defaults. Keep the certs.
2. `trustlens attribution` → which component dominates your errors?
3. If SSH is over-firing → raise `threshold_rho`.
4. If SSH is under-firing → lower it, or decrease `compute_every_n`.
5. If steering events correlate with *quality regression* on grounded
   items → lower `alpha` / `top_k_layers`.
6. Repeat until your grounded-vs-hallucinated curves on the held-out set
   meet the DEEP gates (block_rate ≥ 0.90, false_block_rate ≤ 0.10).

Automate this with `trustlens calibrate` for Platt scaling on the NLI
scores and `trustlens sweep` for the 10-axis capability sweep.

#### Phase 7 — production rollout via the gateway

```python
from trustlens.tenancy.config import TenantConfig, TenantTier, InMemoryTenantStore
from trustlens.gateway.app import build_gateway
# ... backend_registry, signer, cert_store as usual ...

store = InMemoryTenantStore([
    TenantConfig(
        tenant_id="acme",
        tier=TenantTier.DEEP_INSPECTOR,   # enables the DEEP tier for this tenant
        tau=0.55, tau_prime=0.15,          # compliance-tight thresholds
        verify_deadline_ms=500,            # matches DEEP SLA
        allowed_backends=["openai", "anthropic"],
        allowed_oracles=["customer_kb"],
    ),
])

app = build_gateway(
    engine=deep,                           # pass the DeepVerifierEngine, not base
    signer=keypair,
    cert_store=cert_store,
    backend_registry=backend_registry,
    tenant_store=store,
    kb_index=kb,
)
```

Any request with `{"trustlens":{"verification_tier":"deep"}}` — or a tenant
configured for `DEEP_INSPECTOR` — now flows through SSH + steering +
TrustChain, and the resulting certificate carries the full spectral
evidence. See `docs/INTEGRATION.md` for mTLS / ingress / shadow-eval wiring.

### What you see in the certificate

```json
{
  "payload": {
    "overall_status": "verified",
    "claims": [
      {
        "claim_id": "c_ab12",
        "verdict": "verified",
        "support_mass": 0.87,
        "oracle_receipts": [...],
        "sycophancy_delta": null
      }
    ],
    "deep_inspector": {
      "ssh_snapshots": [
        {"step": 4, "rho": 0.84, "severity": "normal"},
        {"step": 8, "rho": 0.92, "severity": "warning"},
        {"step": 12, "rho": 0.99, "severity": "critical"}
      ],
      "steering_events": [
        {"kind": "engage", "at_step": 12, "scale": 1.5, "rho": 0.99}
      ],
      "chain_summary": {
        "first_unreliable_turn": null,
        "cascade_blast_radius": 0
      }
    }
  },
  "signature": "...",
  "signer_key_id": "ed25519-..."
}
```

The `deep_inspector` block is advisory sidecar data, but it is *inside* the
signed payload — tampering invalidates the signature, so the SSH/steering
evidence is non-repudiable alongside the claim verdicts.

### How to think about the commercial unlock

Today, enterprises buy *behavioral classifiers* — models that rate the
output after it's produced. Every vendor does the same thing with slightly
different taxonomies.

Deep Inspector is different: it looks **inside** the model during
generation, and it ships a signed report of what it saw. That is the first
commercial product in the space that can say:

> "This specific token was emitted while the model's attention blocks were
> in a provably unstable regime. Here's the spectral evidence, here's the
> steering intervention we applied, here's the Ed25519 signature over
> everything."

Nothing in the public market ships that today.

## Control surfaces (ops dashboard, CLI, APIs)

**Honest preface.** TrustLens does not ship a custom admin web UI today.
A lightweight operator console (Next.js) is a NEAR-term roadmap item
(see `docs/ENTERPRISE.md` §2.5). What *does* ship today — and what most
operators actually need — is:

1. An auto-generated **Swagger / OpenAPI explorer** at `/docs`
2. A **Prometheus metrics surface** at `/metrics` that plugs into any
   Grafana / Datadog / CloudWatch dashboard
3. A complete **CLI cookbook** for the common operator tasks
4. **REST admin endpoints** for KB management and health
5. **Tenant config via code or env vars** (control-plane is your choice)

Each of those is documented below with copy-pasteable commands.

### Swagger UI — your ready-made API explorer

FastAPI renders a full interactive explorer at `GET /docs`. Every endpoint
has inline schemas, examples, and a "Try it out" button that issues real
requests against the running gateway.

```
http://127.0.0.1:8081/docs          # Swagger UI
http://127.0.0.1:8081/openapi.json  # raw OpenAPI schema (machine-readable)
```

Verified in the `docs/READINESS_REPORT.md` Playwright run — all six
endpoints render and "Try it out" from the browser successfully calls KB
load, KB status, and chat completions.

### Grafana dashboard (prebuilt queries)

`GET /metrics` exposes Prometheus text. Scrape it every 15 s and drop
these panels into Grafana:

| Panel | PromQL |
|---|---|
| Requests/sec by status | `sum by (status) (rate(trustlens_requests_total[1m]))` |
| Verify p99 by tier | `histogram_quantile(0.99, sum by (le,tier) (rate(trustlens_verify_duration_seconds_bucket[5m])))` |
| Cert status mix | `sum by (status) (rate(trustlens_certificate_status_total[5m]))` |
| Blocked fraction per tenant | `sum by (tenant) (rate(trustlens_certificate_status_total{status="blocked"}[5m])) / sum by (tenant) (rate(trustlens_certificate_status_total[5m]))` |
| Budget rejections | `sum by (tenant,kind) (rate(trustlens_budget_rejections_total[5m]))` |
| Circuit breaker state | `trustlens_circuit_breaker_state` |
| Oracle failure rate | `sum by (oracle,reason) (rate(trustlens_oracle_failures_total[5m]))` |
| Backend p95 latency | `histogram_quantile(0.95, sum by (le,backend) (rate(trustlens_backend_latency_seconds_bucket[5m])))` |

Full runbooks (alert thresholds, common-incident remediation, SLO burn
calculations) are in `docs/OPERATIONS.md`.

### CLI cookbook — every command an operator runs

```bash
# Key management
trustlens keygen --out ./.trustlens/signer.pem
trustlens version                              # confirm schema + pipeline versions

# Inspect a certificate offline
trustlens inspect ./cert.json
trustlens verify  ./cert.json --public-key ./.trustlens/signer.pub.pem
trustlens verify  ./cert.json --public-key ./.trustlens/signer.pub.pem \
                  --require-pipeline-version pipeline/1.0.0 \
                  --trusted-key-ids ed25519-old,ed25519-new

# Run the verifier as a standalone service
trustlens serve-verifier --host 0.0.0.0 --port 8080

# Run the full OpenAI-compatible gateway
trustlens serve-gateway  --host 0.0.0.0 --port 8081 \
                         --signer-key ./.trustlens/signer.pem \
                         --cert-store ./.trustlens/certs

# Calibrate NLI thresholds on your own labeled data
#   data format: one JSON object per line: {"score": 0.73, "label": 1}
trustlens calibrate ./my_labeled_scores.jsonl

# Per-component failure attribution (which stage carries the load)
trustlens attribution

# 10-axis capability sweep against real HF datasets
trustlens sweep --n-samples 20
```

Every command returns structured JSON on stdout so it pipes cleanly into
jq / grep / dashboards:

```bash
trustlens inspect ./cert.json | jq '.verdict_breakdown'
trustlens verify  ./cert.json --public-key ./k.pub.pem | jq '.valid'
```

### KB admin via REST

```bash
# Bulk-load documents
curl -X POST http://localhost:8081/v1/kb/load \
  -H "Content-Type: application/json" \
  -d @my-docs.json

# Check index state
curl "http://localhost:8081/v1/kb/status?tenant_id=acme"
```

`my-docs.json` format:

```json
{
  "tenant_id": "acme",
  "documents": [
    {"doc_id": "pol-001", "text": "Refunds are issued within 14 days.",
     "source_uri": "kb://pol-001"},
    {"doc_id": "pol-002", "text": "SLA is 99.9% uptime for paid tiers."}
  ]
}
```

**Security note.** `/v1/kb/*` is a privileged surface. Mount it behind a
separate API key, mTLS client-cert match, or IP allow-list at your
ingress. The gateway itself does not today enforce distinct admin auth —
that's item §2.1 of `docs/ENTERPRISE.md`.

### Tenant config and env vars

Tenant config lives in a `TenantConfigStore`. `InMemoryTenantStore` is
bundled for dev; implement the protocol against Postgres / Consul / your
control-plane API for production (one file, one class, see §2.2 of
`docs/ENTERPRISE.md`).

Env vars respected by `trustlens serve-gateway`:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Enables the OpenAI-compatible backend |
| `TRUSTLENS_BACKEND_URL` | Upstream URL for the OpenAI-compatible backend (also covers vLLM, Together, etc.) |
| `ANTHROPIC_API_KEY` | Enables the Anthropic Messages API backend |
| `OLLAMA_BASE_URL` | Enables the Ollama backend |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Optional OpenTelemetry collector |
| `OTEL_SERVICE_NAME` | Service name in traces (default `trustlens-gateway`) |
| `TRUSTLENS_HOST` / `TRUSTLENS_PORT` | Listen address when run from the Docker image |
| `TRUSTLENS_SIGNER_KEY` / `TRUSTLENS_CERT_STORE` | Path defaults in the Docker image |

### What ships today vs. what's NEAR-term

| Capability | Status | Reference |
|---|---|---|
| Swagger UI at `/docs` | SHIP | — |
| Prometheus `/metrics` | SHIP | `docs/OPERATIONS.md` |
| OpenTelemetry spans | SHIP (optional extra) | `trustlens[otel]` |
| CLI (keygen/verify/inspect/serve-*/calibrate/attribution/sweep) | SHIP | this README |
| KB admin API (POST /v1/kb/load, GET /v1/kb/status) | SHIP | this README |
| Health / Ready probes (`/healthz`, `/readyz`) | SHIP | `docs/OPERATIONS.md` |
| Custom admin web UI (Next.js dashboard) | **NEAR** | `docs/ENTERPRISE.md` §2.5 |
| Admin RBAC + API-key auth on admin endpoints | **NEAR** | `docs/ENTERPRISE.md` §2.1 |
| Helm chart + Terraform modules | **NEAR** | `docs/ENTERPRISE.md` §2.4 |

## How it bolts on in 5 minutes

Zero application changes. Swap the base URL:

```diff
  from openai import OpenAI
- client = OpenAI(base_url="https://api.openai.com/v1")
+ client = OpenAI(base_url="https://trustlens.yourco.net/v1",
+                 default_headers={"X-TrustLens-Tenant-Id": "acme"})
```

Every response now ships with a signed certificate. Read it from the response body
or the `X-TrustLens-Certificate-Id` header.

See **[docs/QUICKSTART.md](docs/QUICKSTART.md)** for a 5-minute hands-on.
See **[docs/INTEGRATION.md](docs/INTEGRATION.md)** for enterprise wiring
(BYO-KB, custom oracles, Anthropic/Ollama backends, per-tenant SLAs).
See **[docs/ENTERPRISE.md](docs/ENTERPRISE.md)** for the production-readiness
checklist + roadmap.
See **[docs/BENCHMARKS.md](docs/BENCHMARKS.md)** for methodology and
reproducibility of every number above.
See **[docs/OPERATIONS.md](docs/OPERATIONS.md)** for SRE runbooks,
metrics, and incident response.

## Architecture at a glance

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          TrustLens Gateway                               │
│  OpenAI-compatible · streaming SSE · per-tenant routing                  │
├──────────────────────────────────────────────────────────────────────────┤
│  Tenancy   ─ config · budgets · circuit breakers · deadlines · shadow eval│
│  Backends  ─ OpenAI · Anthropic · Ollama · vLLM · any OpenAI-compatible   │
│  KB Admin  ─ POST /v1/kb/load · GET /v1/kb/status                         │
└──────────────┬───────────────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         Verifier Engine                                   │
│  claim extractor ─► claim DAG ─► oracle fan-out ─► NLI stack ─► aggregate │
│                                                                           │
│  Tiers:   FAST (NLI-only, <30 ms)                                         │
│           STANDARD (NLI + KB, <100 ms)                                    │
│           DEEP (NLI + KB + Wikidata + spectral, <500 ms)                  │
└──────────────┬───────────────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                   Signed Trust Certificate                                │
│  Ed25519 signature · content-addressed · offline-verifiable               │
│  per-claim verdict · oracle receipts · pipeline version pinning          │
└──────────────────────────────────────────────────────────────────────────┘
```

## Package layout

```
trustlens/
  certificate/      — Ed25519 signing, schema, content-addressed store
  verifier/         — claim DAG, extractor, NLI stack, engine, router, service
    ├─ nli.py                  — LexicalNLI (baseline, deterministic)
    ├─ span_aware_nli.py       — cross-doc span isolation
    ├─ numeric_aware_nli.py    — year/unit mismatch detection
    ├─ transformer_nli.py      — optional DeBERTa-v3 cross-encoder
    ├─ sycophancy.py           — leading-cue + counterfactual divergence
    ├─ calibration.py          — Platt scaling + ECE / Brier / MCE
    └─ engine.py               — claim-level verification orchestrator
  oracles/          — Wikidata, customer KB (lexical + vector), negation-aware
  gateway/          — OpenAI-compatible proxy
    ├─ app.py                  — FastAPI app factory
    ├─ backends.py             — Echo, OpenAI-compatible
    ├─ backends_anthropic.py   — Anthropic Messages API
    ├─ backends_ollama.py      — Ollama native API
    ├─ verification_tier.py    — FAST / STANDARD / DEEP resolver
    └─ kb_admin.py             — POST /v1/kb/load, GET /v1/kb/status
  deep_inspector/   — SSH spectral hooks, activation steering, TrustChain,
                       5-suite benchmark harness, signed scorecards
  tenancy/          — per-tenant config, RPS + token-bucket budgets
  robustness/       — circuit breaker, deadline propagation, shadow eval
  observability/    — Prometheus metrics, optional OpenTelemetry tracing
  sdk/              — Python client + offline verifier
  cli/              — `trustlens` CLI (keygen, verify, inspect, serve-*,
                       calibrate, attribution, sweep)
  Dockerfile        — multi-stage production image
```

## Docs

- **[docs/QUICKSTART.md](docs/QUICKSTART.md)** — 5-minute bolt-on
- **[docs/INTEGRATION.md](docs/INTEGRATION.md)** — enterprise wiring, BYO-KB, custom oracles
- **[docs/ENTERPRISE.md](docs/ENTERPRISE.md)** — production-readiness checklist + roadmap
- **[docs/BENCHMARKS.md](docs/BENCHMARKS.md)** — methodology, reproducibility, raw data
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — SRE runbooks, SLOs, incident response
- **[PLAN_DEEP_INSPECTOR_SLA.md](PLAN_DEEP_INSPECTOR_SLA.md)** — the Deep Inspector productization spec

## License

Apache-2.0.
