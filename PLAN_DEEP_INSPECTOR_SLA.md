# Deep Inspector Tier — Production SLA Plan

**Scope.** Make SSH (Spectral Stability Hook), RAD-CoT (activation steering), and Agentic Chain Propagation production-ready as the top-tier `Deep Inspector` SKU with published, enforceable SLAs and reproducible quality/performance benchmarks.

**Non-goals.** These features remain *opt-in on-prem / private VPC*. They are not offered in the shared Gateway tier because they require model-internal hooks.

**Owner.** Deep Inspector team (to be staffed). Depends on Gateway + Verifier teams for shared plane.

---

## 1. Why these three features need a separate tier

| Feature | Why it's high-value | Why it's hard to operate |
|---|---|---|
| **SSH spectral radius** | Pre-hallucination *early warning signal* — fires BEFORE the model emits the bad token. Uniquely enables preventive interventions. | Requires attention hooks → tight model coupling. Adds ≥5–15% decode latency. |
| **RAD-CoT steering** | Corrects drift in real-time without retraining. Pareto-characterized safety/capability knob. | Additive to residual stream on hot path. Wrong scale → capability collapse. |
| **Agentic chain propagation** | Extends trust certificates across multi-turn chains; catches *cascade failures* where step N silently depends on a hallucinated step 1. | Stateful across turns → session store, eviction, replay semantics. |

All three fundamentally require either a colocated model or deep hooks. Customers must self-host (with our Helm chart) or run in a dedicated VPC instance.

---

## 2. SLA contract (what we commit to the customer)

### 2.1 Availability & latency

| Metric | Commitment | Measurement |
|---|---|---|
| Service availability | **99.9%** per calendar month | External synthetic probes, 30s interval, 3-region consensus |
| p50 Deep Inspector overhead over baseline generate | **≤ 80 ms** per 1K output tokens | Per-request histogram, tenant-scoped |
| p95 Deep Inspector overhead | **≤ 180 ms** per 1K output tokens | " |
| p99 Deep Inspector overhead | **≤ 400 ms** per 1K output tokens | " |
| Steering engagement decision latency | **≤ 15 ms** from SSH alarm to residual update | Span-level trace |
| Certificate signing | **≤ 5 ms** p99 | " |
| Agentic chain step verification | **≤ 250 ms** p95 per step | " |

### 2.2 Quality

| Metric | Commitment | Source |
|---|---|---|
| Hallucination reduction (TruthfulQA MC1) | **≥ 35%** relative to baseline model | Nightly eval, published scorecard |
| Collateral capability loss (10-axis Pareto, default α) | **≤ 5%** mean drop | Nightly eval |
| SSH alarm precision (spectral alarm ⇒ actual hallucination or drift) | **≥ 0.70** | Gold-labeled ground truth set |
| SSH alarm recall | **≥ 0.60** | " |
| Cascade failure detection rate (agentic chains) | **≥ 0.80** | Labeled chain benchmark |
| Cascade failure false-positive rate | **≤ 0.10** | " |

### 2.3 Credits

| SLO missed | Credit |
|---|---|
| Availability < 99.9% | 10% monthly fee credit per 0.1% below target (capped at 100%) |
| Latency SLO missed > 2% of requests in a calendar day | 5% monthly credit per occurrence (max 3/month) |
| Quality floor breached on nightly eval (two consecutive runs) | Immediate notification + 5% credit + remediation plan within 7 days |

### 2.4 Exclusions

- Customer model quality baseline — we improve a model, we don't rescue a broken one. Baseline must pass our pre-flight benchmark (≥ 40% TruthfulQA MC1 uncorrected).
- Customer infrastructure: nodes without the attested Helm deployment are out of scope.
- Deliberate adversarial prompts targeting our published interventions (these go through Shadow Eval, not SLA).

---

## 3. Architecture required for production-grade operation

### 3.1 SSH (Spectral Stability Hook)

**Current state.** Python hook attached via `register_forward_hook`, power-iteration on attention weights every N tokens. Good for research, too slow for SLA latency.

**Production rewrite needed:**

1. **Rewrite the spectral radius kernel in CUDA** as a fused kernel inside the transformer layer's attention forward. Deliverable: a single `spectral_update(attn_scores, state) -> rho` kernel invoked inline.
2. **Sparse power iteration:** 3 iterations instead of 20 (validated to converge within 1% for attention matrices of rank ≤ 128).
3. **Layer sampling, not all-layer:** monitor top-K discriminative layers only (K=4), computed offline per model via gradient attribution on a TruthfulQA dev set.
4. **Ring-buffer telemetry:** ρ history lives in pinned host memory, zero-copy read by the steering scheduler.
5. **Kill-switch:** if hook overhead > 20% for 30 consecutive tokens, auto-detach and emit degraded-mode certificate.

**Target:** ≤ 8% p99 decode overhead with K=4 layers, every-5-token cadence.

**Files to create / port:**
- `trustlens/deep_inspector/ssh/kernel.cu` — fused kernel
- `trustlens/deep_inspector/ssh/hook.py` — loader, host-side state machine
- `trustlens/deep_inspector/ssh/calibration.py` — offline layer-selection job
- `trustlens/deep_inspector/ssh/killswitch.py` — latency watchdog

### 3.2 RAD-CoT (activation steering)

**Current state.** Per-layer mean-difference vectors applied via forward hooks on residual stream. Scale set by a hand-tuned function of ρ.

**Production rewrite needed:**

1. **Pre-compile steering as model-level artifacts.** Steering vectors are computed once per model version, stored alongside the weights, and loaded as constant tensors. No runtime contrastive-pair computation.
2. **Adaptive scale learned, not hand-tuned.** Fit a piecewise-linear `scale(rho, layer_idx, context_type)` from Pareto sweep data. Serialize as a small lookup table.
3. **Fused residual add:** steering add-op fused with LayerNorm on the residual path. Avoids an extra kernel launch per layer per token.
4. **Safety clamp:** hard cap on steering magnitude as percentage of residual norm, not absolute α. Prevents catastrophic capability loss even under adversarial ρ spikes.
5. **Per-tenant contrastive overrides.** Tenants can supply domain-specific contrastive pairs; we bake a tenant-specific steering delta on top of the base vector.

**Target:** ≤ 3 ms per decode step added, zero config required beyond choosing tier.

**Files to create / port:**
- `trustlens/deep_inspector/rad_cot/precompute.py` — offline steering-vector builder + Pareto fit
- `trustlens/deep_inspector/rad_cot/runtime.py` — hook loader, safety clamp
- `trustlens/deep_inspector/rad_cot/fused_add.cu` — fused steering + LayerNorm
- `trustlens/deep_inspector/rad_cot/tenant_delta.py` — tenant override layer

### 3.3 Agentic chain propagation

**Current state.** Single-request pipeline. Multi-turn benchmarks exist (`scripts/extreme_benchmark.py` Phase 3) but no runtime session store.

**Production build needed:**

1. **Chain session store.** Redis-backed, tenant-namespaced. Entry: `(chain_id, step_idx) → { claim_dag, cert_id, rho_history, active_steering }`. TTL default 24h, configurable.
2. **Transitive trust propagation.** Step N's certificate must reference its predecessor's `cert_id`. If any predecessor has status `BLOCKED`, step N is forced to BLOCKED too (cascade failure).
3. **Replayable sessions.** Every chain can be replayed from stored claim DAGs for audit or debugging. No need to re-run the LLM.
4. **Cascade-detector trigger.** A claim in step N that depends on a step-1 claim flagged as `UNSUPPORTED` triggers an *automatic re-verification with broader oracle set* before emission.
5. **Forget-and-rebuild semantics.** Tenants can mark a step as "corrected by user" → downstream steps re-verify against the corrected prefix.
6. **Conversation certificate.** A chain-level signed certificate that references every step-cert; this is the artifact auditors actually want.

**Target:** cascade detection ≥ 80% recall, ≤ 10% FPR. Session-store lookup p99 ≤ 5 ms.

**Files to create / port:**
- `trustlens/deep_inspector/chain/session_store.py` — Redis backend + protocol
- `trustlens/deep_inspector/chain/propagation.py` — trust edge computation
- `trustlens/deep_inspector/chain/cascade.py` — cascade detector
- `trustlens/deep_inspector/chain/conversation_cert.py` — chain-level cert builder

---

## 4. Quality benchmark harness (what we measure, nightly, against SLO)

### 4.1 Benchmark suites (all published, all reproducible)

| Suite | Metric | Tests which SLO |
|---|---|---|
| `bench/truthfulqa_mc1.py` | MC1 accuracy delta | Quality: hallucination reduction |
| `bench/pareto_10axis.py` | Capability drop across 10 axes at α ∈ [0, 5] | Quality: collateral |
| `bench/ssh_gold.py` | SSH alarm precision/recall vs labeled hallucinations | Quality: SSH P/R |
| `bench/rad_cot_stability.py` | capability variance under steering | Quality: RAD-CoT safety |
| `bench/chain_cascade.py` | Cascade detection P/R on labeled chains | Quality: cascade detect |
| `bench/latency_p99.py` | per-SLO latency on representative traffic mix | Latency SLOs |
| `bench/adversarial.py` | bypass-attack success rate | Robustness |

Each suite emits a JSON scorecard signed with the Deep Inspector build's Ed25519 key. Scorecards are published to the tenant's audit bucket.

### 4.2 Gold-label ground truth (the hardest thing to build)

- **SSH gold set:** 5,000 prompts × model completions, each labeled by 3 annotators with {hallucinated-at-span, drift-no-halluc, clean}. Required because otherwise SSH P/R is unmeasurable.
- **Agentic chain gold set:** 500 labeled chains with explicit cascade points. Each chain has a ground-truth trust edge per step.
- **Recalibration cadence:** quarterly; drift detected via jaccard on labels vs. ensemble agreement.

**Plan item:** spin up a labeling vendor contract in Q1. This is the single biggest gating item for SLA — we cannot promise SSH P/R ≥ 0.70 without a defensible measurement.

### 4.3 Continuous measurement

- Nightly full-suite run on a pinned baseline (Qwen3.5-35B-A3B for internal; each tenant's model for tenant scorecards).
- Every build to staging runs a smoke subset (20% sample) in < 10 min.
- Shadow Eval: 1% sampled live traffic goes through a labeling queue + agreement check. Drift alert if rolling 7-day SSH precision drops > 5 pp.

---

## 5. Performance benchmark harness

### 5.1 Load profile

| Tier | Concurrent reqs | Avg output tokens | Target p99 latency |
|---|---|---|---|
| dev | 10 | 128 | 500 ms |
| pro | 100 | 512 | 800 ms |
| enterprise | 1000 | 2048 | 1500 ms |

### 5.2 Rig

- **Hardware:** dedicated H100 or GB10 benchmark nodes; exact SKU pinned per benchmark run.
- **Generator:** `locust` or `k6` driving OpenAI-compatible requests with controlled token-length distribution.
- **Harness:** `bench/perf/run.py` orchestrates warm-up, steady-state, cool-down; emits latency histograms + GPU utilization time-series.
- **Gate:** a build cannot ship if any p99 latency SLO breaches on the enterprise profile.

### 5.3 Capacity planning outputs

Every release produces:
- `capacity-{release}.json`: tokens/sec/GPU for each tier
- `cost-per-1k-tokens-{release}.json`: derived ops cost
- `overhead-attribution-{release}.json`: how much latency each of {SSH, RAD-CoT, agentic, oracles, cert signing} contributes

These feed the pricing model for the Deep Inspector SKU.

---

## 6. Robustness: what breaks and what we do

| Failure | Detection | Response |
|---|---|---|
| SSH kernel regression inflates latency | Kill-switch (3.1.5) + p99 watchdog | Auto-detach hooks, emit degraded-mode cert, page on-call |
| Steering causes capability collapse | Nightly Pareto eval trips a guard | Revert to last-known-good steering artifact, block deploy |
| Redis session store outage | Health probe | Fail open: continue without chain propagation, mark certs `degradations:["chain_unavailable"]` |
| GPU OOM under load | Auto-scaler + ahead-of-time admission control | Reject with 429, suggest lower tier |
| Model weights drift vs. steering artifact | Hash check at load | Refuse to start; require recomputed steering |
| Cascade detector false-positive storm | Rolling FPR > 20% | Raise alarm threshold, page on-call, ship fix in 24h |
| Adversarial prompt defeats steering | Shadow Eval detection | Add to regression set, update steering in next release |

---

## 7. Rollout and gate criteria

Gate each feature separately. No feature enters the `Deep Inspector` tier until all gates pass.

### 7.1 SSH gates
- [ ] Kernel p99 overhead ≤ 8% on enterprise profile
- [ ] Alarm P ≥ 0.70, R ≥ 0.60 on gold set
- [ ] 14-day soak test: zero kill-switch trips under steady load
- [ ] Offline calibration job reproducible on three model families (Llama-3, Mistral, Qwen)

### 7.2 RAD-CoT gates
- [ ] Pareto sweep shows ≤ 5% mean capability drop at default α
- [ ] Safety clamp verified under synthetic adversarial ρ spike
- [ ] Tenant-override pipeline documented, two design-partner tenants onboarded
- [ ] Determinism: same input + α → same output within fp16 noise (hash check)

### 7.3 Agentic chain gates
- [ ] Cascade detector P ≥ 0.80 / R ≥ 0.80 on labeled chains
- [ ] Redis failover: chain propagation degrades cleanly, tested in chaos harness
- [ ] Conversation cert verifies offline with no network access
- [ ] Replay-from-store reconstructs identical trust graph bit-for-bit

### 7.4 Joint gates
- [ ] End-to-end SLO met on enterprise profile for 7 consecutive days
- [ ] External red-team report: no critical bypasses
- [ ] Runbook reviewed by on-call rotation
- [ ] Status page integrated; incident retro template published

---

## 8. Deliverables and staffing

### 8.1 Deliverables (first release)

1. `trustlens/deep_inspector/` package (SSH, RAD-CoT, chain subpackages)
2. Helm chart `trustlens-deep-inspector` for on-prem / VPC deploy
3. Benchmark harness under `bench/` with signed scorecards
4. SLA document (this plan's §2 as a legal-approved contract annex)
5. Runbook + status page
6. Customer-facing docs: integration, tenant-override guide, audit-cert verification CLI

### 8.2 Staffing (minimum)

| Role | Headcount |
|---|---|
| Systems engineer (CUDA kernels, hooks) | 1 |
| ML engineer (steering, calibration) | 1 |
| Backend engineer (chain session store, certs) | 1 |
| SRE (deploy, observability, on-call rotation) | 1 |
| Eval/labeling lead | 1 (part-time acceptable) |
| Product / SLA owner | 0.5 |

### 8.3 Sequencing

| Week | Focus |
|---|---|
| 1–2 | Port SSH + RAD-CoT into `deep_inspector/`, wire to verifier service |
| 3–4 | Build offline calibration pipeline; lock steering-artifact format |
| 5–6 | Benchmark harness; run baseline Pareto + SSH P/R |
| 7–8 | Session store + chain propagation + conversation cert |
| 9–10 | Shadow Eval integration; gold-label vendor spin-up |
| 11–12 | Soak test, chaos test, red team |
| 13 | Design-partner launch |
| 14–16 | GA gates, SLA publish |

---

## 9. Open questions (decide before committing SLA publicly)

1. **Kernel portability.** Are we committing to H100 + GB10 only, or also L40S / A100? Kernels must be validated per SKU.
2. **Gold-label budget.** Vendor quote for 5,000-item SSH gold set + 500-chain gold set: estimate needed this week.
3. **Tenant override boundary.** If a tenant ships their own contrastive pairs, whose SLA applies? Propose: SSH/RAD-CoT SLOs reset to "best effort" on tenant-supplied artifacts; only our baseline is covered.
4. **Pricing.** Deep Inspector presumably 3-5× base Gateway. Need finance sign-off after capacity numbers from §5.3.
5. **Model-family coverage.** Initial GA on exactly which model families? Recommendation: Llama-3 + Qwen-3 only; expand per tenant.

---

## 10. What this plan does NOT commit to

- No token-level intervention beyond steering (keeps us out of low-level decoding hacks that break with every model-arch change).
- No claims about "eliminating hallucination" — only *reducing* with measurable P/R.
- No external-model SLA (e.g., GPT-4 behind the gateway); Deep Inspector is on-prem/VPC only.
- No fine-tuning or training commitments — steering is inference-time only.

---

*This plan is a contract between engineering and product. Sign-off required from: Eng lead, SRE lead, Product, Legal (for the SLA annex), Finance (for pricing), Security (for the attested deploy).*
