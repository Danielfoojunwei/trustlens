# Local TrustLens on GB10 — the end-user experience

A single script brings TrustLens up on your workstation (optimised for
NVIDIA GB10 / DGX Spark but it runs anywhere). You get:

- a running gateway + signed-cert pipeline,
- a browser dashboard with a chat playground,
- a local LLM (Ollama + `llama3.1:8b`) behind the gateway so you can
  actually watch hallucinations get caught,
- an MCP stdio server ready to wire into Claude Desktop / Code / Cursor,
- the 10 000-item TrustLens-10k adversarial benchmark,
- the penetration + overload test battery.

Everything runs from one entry-point:

```bash
./scripts/launch_local.sh
```

---

## What each mode does

### `1` — TrustLens-10k adversarial benchmark (CPU, ~3 s)

10 000 hand-synthesised items across 10 axes that exercise exactly the
things TrustLens's architecture catches that a classifier-only guardrail
cannot: numeric/year mismatches, negation flips, cross-doc
contradictions, anaphora chains, sycophancy leading cues, prompt
injection, jailbreak suffixes, multi-turn cascades, PII leak traps, and
span-isolation compound attacks.

Latest signed scorecard, default seed, LEXICAL tier:

| axis | block rate | false-block rate | gate | result |
|---|---|---|---|---|
| numeric_year_mismatch    | **0.658** | 0.000 | 0.65 | PASS |
| negation_flip            | **0.933** | 0.000 | 0.80 | PASS |
| cross_doc_contradiction  | **1.000** | 0.000 | 0.75 | PASS |
| anaphora_chain           | **1.000** | 0.000 | 0.70 | PASS |
| sycophancy_leading_cue   | **0.993** | 0.000 | 0.60 | PASS |
| prompt_injection         | **0.992** | 0.000 | 0.80 | PASS |
| jailbreak_suffix         | **0.973** | 0.000 | 0.75 | PASS |
| multi_turn_cascade       | **1.000** | 0.000 | 0.65 | PASS |
| pii_leak_trap            | **1.000** | 0.000 | 0.80 | PASS |
| span_isolation_compound  | **1.000** | 0.000 | 0.65 | PASS |

**Aggregate: 10 / 10 axes PASS**, 10 000 items, 3.3 s total, Ed25519
scorecard `results/trustlens_10k/scorecard-*.json`.

Honest reading: this is the LEXICAL tier — no transformer model, no
GPU. The numeric axis at 0.658 reflects the LEXICAL ceiling on the
harder sub-templates (comma-formatted currency, `"1980s"` decade
suffixes). The NLI and DEEP tiers target 0.85 and 0.95 respectively.

### `2` — Penetration + overload battery (CPU, ~40 s)

26 attack vectors across auth, injection/KB-poisoning, and
streaming/resilience, plus a 1 → 1000 RPS overload ramp. Latest full
run:

| category | vector | result |
|---|---|---|
| auth      | admin_without_auth · wrong_password · unknown_tenant · viewer_blocked_from_mutation · malformed_bearer · malformed_chat_body · viewer_role_escalation · revoked_session_replay · unrecognized_auth_headers · fake_session_cookie | **10 / 10 PASS** |
| injection | kb_poisoning_negation · prompt_injection_in_message · webhook_ssrf_configuration · oversized_prompt_2mb · utf8_emoji_bidi_zwsp · kb_bulk_500_docs · oidc_start_without_provider · path_traversal_tenant_id · json_deep_nesting · cert_issuance_flow | **10 / 10 PASS** |
| streaming | sse_happy_path · sse_client_disconnect · concurrent_chats_x20 · readyz_under_load · prometheus_metrics_exposure · openapi_surface_complete | **6 / 6 PASS** |

**Overload ramp**:

| target RPS | actual RPS | ok rate | p99 latency |
|---|---|---|---|
| 1 | 2.0 | 50.0% | 3.4 ms |
| 10 | 10.3 | 100.0% | 4.3 ms |
| 50 | 50.2 | 22.8% | 2.5 ms |
| 100 | 100.1 | 10.0% | 2.9 ms |
| 250 | 249.7 | 4.0% | 2.7 ms |
| 500 | 496.9 | 2.0% | 4.5 ms |
| **1000** | **513.2** | 1.9% | 1119 ms |

The "ok rate" drops as expected because the **token-bucket budget
tracker** is firing 429 rate-limits — that's the *correct* defensive
behaviour. What matters: **no 5xx at any band**, p99 stays below 5 ms
until saturation at 500+ RPS.

### `3` — Interactive chat + dashboard

Brings up Ollama with `llama3.1:8b` (installs both if missing) and
points the gateway at it. Then opens the operator dashboard at
`http://127.0.0.1:8081/dashboard`, plus prints the MCP config block to
paste into Claude Desktop / Code.

From the dashboard's chat playground you can:

- pick a verification tier (FAST / STANDARD / DEEP)
- toggle your tenant's tau / tau_prime
- send prompts and see the response + the signed certificate
  annotation side-by-side
- mask or reveal any `unsupported` / `contradicted` claim the verifier
  flagged
- see the 3-axis live panel (internal ρ, external support, sycophancy)
  update in real time

### `4` — Everything, sequential

Runs 1 → 2 → 3 in that order. Memory-safe: benchmark and pentest never
touch the GPU; chat is the only mode that starts Ollama.

---

## Recommended hardware footprint

| Component | CPU | RAM | VRAM | Notes |
|---|---|---|---|---|
| Gateway + verifier (LEXICAL) | 1 core | ~200 MB | 0 | always on |
| 10k benchmark | 1 core | ~400 MB | 0 | 3 s to complete |
| Pentest battery | 2 cores | ~600 MB | 0 | 40 s, includes overload ramp |
| Ollama + `llama3.1:8b` | 2 cores | ~1 GB host | ~6 GB | chat mode only |

On your GB10 (121 GB unified memory), running modes 1, 2, and 3
simultaneously is safe. **Do not** start a 35B vLLM server at the same
time as Ollama — they'll contend for unified memory and likely OOM.

## Where the artefacts live

```
results/
  trustlens_10k/
    scorecard-<ts>.json          signed, verifiable with `trustlens verify`
    scorecard-<ts>.summary.txt   human-readable summary
  pentest/
    pentest-<ts>.json            signed, same key
.trustlens/
  signer.pem                      Ed25519 private key (chmod 600)
  signer.pub.pem                  public key — hand to auditors
  certs/<tenant>/<...>.json       one signed cert per chat response
```

## Offline verification example

```bash
# anyone with the public key can verify a scorecard without touching
# the gateway — proves both WHAT the verifier claimed AND that the
# run was signed by a specific key id.
python3 -c "
import json
from base64 import b64decode
from trustlens.certificate.signer import KeyPair, payload_digest, load_public_key_pem

pub = load_public_key_pem(open('.trustlens/signer.pub.pem', 'rb').read())
doc = json.load(open('results/trustlens_10k/scorecard-<ts>.json'))
digest = payload_digest(doc['payload'])
assert digest == doc['scorecard_id']
pub.verify(b64decode(doc['signature']), digest.encode())
print('OK — scorecard is authentic')
"
```

## Reproducing the benchmark on your own corpus

```bash
# 1. write your own items to a JSONL (same schema as BenchItem)
# 2. extend trustlens/benchmarks/trustlens_10k/generators with your axis
# 3. regenerate the committed .jsonl.gz
python3 scripts/generate_trustlens_10k.py --seed 42

# 4. run
python3 scripts/run_trustlens_10k.py \
    --out-dir ./results/trustlens_10k \
    --signer-key ./.trustlens/signer.pem
```

## Hooking it into an agent

```bash
trustlens mcp serve --transport stdio \
    --gateway-url http://127.0.0.1:8081 \
    --tenant-id demo
```

Then point Claude Desktop / Code / Cursor at the command via their
MCP config. The 53 tools exposed (`trustlens mcp tools`) let the agent
do everything the dashboard does — and more — on your behalf while you
stay in control of every destructive action.

See `docs/AGENTIC.md` for the full MCP / plugin / skills walkthrough
and `plugin/skills/trustlens-setup/SKILL.md` for the agentic onboarding
playbook.
