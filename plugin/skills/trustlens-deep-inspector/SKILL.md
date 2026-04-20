---
name: trustlens-deep-inspector
description: Configure, tune, and operate the DEEP tier — Spectral Stability Hooks (SSH), activation steering (RAD-CoT), and TrustChain agentic cascade detection. Use whenever the user asks about deep verification, mechanistic interpretability, ρ thresholds, steering vectors, or "what's happening inside the model".
---

# Deep Inspector

Deep Inspector ships three rare-in-production research techniques as
production tools:

1. **SSH** — power-iteration on attention weight matrices to estimate
   the spectral radius ρ. Spikes in ρ precede hallucinations.
2. **Activation steering** — forward hooks that add a per-layer
   steering vector when SSH fires critical alarms.
3. **TrustChain** — DAG across turns; flags the first unreliable turn
   and its blast radius in agentic flows.

## Phase 0 — Decide if DEEP is needed

Use **LEXICAL** if the user is doing low-stakes chat, content
generation, or simple FAQ. DEEP is overkill (and 100× slower).

Use **DEEP** if any of:
- Compliance regime requires traceability beyond output classification
  (EU AI Act high-risk, FDA GMLP, HIPAA-GenAI).
- Multi-turn agent where a first-turn fabrication compounds.
- Red-teaming the user's own model with pre-emit signals.

Always ask the user to confirm before enabling DEEP — it changes the
latency budget from sub-ms to 500 ms p99.

## Phase 1 — Verify the environment

The DEEP tier needs torch + transformers + a HF causal LM. Check by
shelling out:

```bash
pip install -e '.[nli]'
python3 -c "import torch, transformers; print(torch.cuda.is_available())"
```

If CUDA isn't available the user can still run on CPU but should know
p99 will likely miss the 500 ms gate.

## Phase 2 — Enable per-tenant default

`settings_update(deep_inspector_default=True)` flips the default for
DEEP_INSPECTOR-tier tenants. For per-request use, pass
`{"verification_tier":"deep"}` in chat (already exposed via `chat`
tool's `tier` arg).

## Phase 3 — Tune SSH

The 3 SSH knobs and their tuning heuristics:

| Knob | Default | Raise if… | Lower if… |
|---|---|---|---|
| `ssh_threshold_rho` | 0.97 | too many critical alarms on grounded answers | hallucinations slip through with no critical alarm fired |
| `ssh_compute_every_n` | 4 | p99 misses 500 ms gate | fabrications are short-lived and need finer-grained alarms |
| `power_iter_steps` (in code) | 20 | ρ estimates look noisy | ρ estimation is the latency bottleneck |

Use `settings_update(ssh_threshold_rho=0.95, ssh_compute_every_n=8)`
to apply.

After each tune: run a small representative batch through `chat(tier="deep")`
and look at the resulting `axes_recent()` distribution + `incidents_list(kind="ssh.critical")`
counts.

## Phase 4 — Tune steering

Same shape:

| Knob | Default | Raise if… | Lower if… |
|---|---|---|---|
| `steering_alpha` | 1.5 | steering doesn't change output enough | steering degrades grounded prompts |
| `steering_top_k_layers` | 4 | single-layer steering insufficient | over-correction → off-topic |

Apply via `settings_update(steering_alpha=2.0, steering_top_k_layers=6)`.

## Phase 5 — Diagnose live behavior

When the user asks "what is Deep Inspector doing right now":

1. `axes_summary(window_s=300)` → 3-axis (internal/external/sycophancy) means.
2. `axes_recent(limit=50)` → individual cert points; look for ρ spikes.
3. `incidents_list(kind="ssh.critical", limit=20)` → recent critical
   alarms; each has a `cert_id` + step + ρ in `detail`.
4. `incidents_list(kind="radcot.engage", limit=20)` → steering events;
   each has `scale` + `at_step` + `rho`.
5. `verify_certificate(cert_id)` on any of these → see the full
   `deep_inspector` sidecar block in the cert payload.

## Phase 6 — Steering vectors (out-of-band)

The MCP server doesn't compute steering vectors — that's a one-off
offline job (contrastive activation addition). Walk the user through:

1. Build a positive-prompt corpus (grounded answers) and a
   negative-prompt corpus (the failure mode they want to correct).
2. Run the `compute_steering_vectors` recipe in `README.md` § Phase 3.
3. Save the resulting `dict[int, torch.Tensor]` to disk.
4. Pass to `RealSteeringAdapter(steering_vectors=...)`.

Re-train any time they fine-tune the underlying model or observe
steering regressions.

## Important

DEEP costs ~100× more compute than LEXICAL. The agent must surface
this trade-off explicitly when offering to enable it.
