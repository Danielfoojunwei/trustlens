# QUICKSTART — 5-minute bolt-on

This guide gets TrustLens in front of your LLM in five minutes, using the echo
backend (no external API key needed) so you can see certificates flow end-to-end.

> If you're reading this, you probably just want to answer:
> *"What does an OpenAI call with TrustLens in front of it look like, and
> what comes back?"*
> This guide answers that. Enterprise wiring (custom KB, Anthropic/Ollama
> backends, SLA tiers, per-tenant budgets) is in `docs/INTEGRATION.md`.

---

## 1. Install

```bash
# from the repo root
pip install -e .
```

Optional extras (install only what you need):

```bash
pip install -e '.[nli]'          # transformer NLI + vector KB
pip install -e '.[calibration]'  # Platt scaling / ECE for `trustlens calibrate`
pip install -e '.[anthropic]'    # Anthropic backend
pip install -e '.[sweep]'        # 10-axis capability sweep via HF datasets
pip install -e '.[otel]'         # OpenTelemetry tracing
pip install -e '.[all]'          # everything
```

## 2. Generate a signer keypair

Every certificate is signed by an Ed25519 key. Produce one:

```bash
trustlens keygen --out ./.trustlens/signer.pem
```

Outputs:
- `./.trustlens/signer.pem` — private key (keep this secret)
- `./.trustlens/signer.pub.pem` — public key (distribute to verifiers/auditors)
- prints the derived `key_id` as JSON

Rotate keys on a schedule; auditors verify the `key_id` embedded in each cert.

## 3. Start the gateway

```bash
trustlens serve-gateway --host 0.0.0.0 --port 8081
```

This starts a FastAPI app with:
- `POST /v1/chat/completions` — OpenAI-compatible, streaming or buffered
- `POST /v1/kb/load` — bulk-load KB documents
- `GET  /v1/kb/status` — KB index stats
- `GET  /healthz` — liveness
- `GET  /readyz` — readiness
- `GET  /metrics` — Prometheus scrape

Default demo config: one tenant (`demo`, PRO tier), echo backend.

## 4. Call it like OpenAI

```bash
curl -s -X POST http://localhost:8081/v1/chat/completions \
  -H "X-TrustLens-Tenant-Id: demo" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "echo",
        "messages": [
          {"role": "user", "content": "What is the capital of France?"}
        ]
      }' | jq .
```

The response includes a `trustlens` annotation block:

```json
{
  "id": "chatcmpl-...",
  "choices": [ ... ],
  "trustlens": {
    "certificate_id": "sha256:abc123...",
    "certificate_status": "partial",
    "pipeline_version": "pipeline/1.0.0",
    "renderable_text_hash": "sha256:...",
    "masked_claim_ids": [],
    "degradations": []
  }
}
```

The header `X-TrustLens-Certificate-Id` carries the same ID for operators.

## 5. Pull the certificate and verify it offline

Read the raw certificate JSON from the cert store (default
`./.trustlens/certs/{first-2}/{rest}.json`) or mount a filesystem store somewhere
stable. Then:

```bash
trustlens verify ./.trustlens/certs/ab/abc123...json \
  --public-key ./.trustlens/signer.pub.pem
```

Exit code `0` = signature valid, `1` = tampered or wrong key.

You can also pretty-print the verdict breakdown:

```bash
trustlens inspect ./.trustlens/certs/ab/abc123...json
```

## 6. Point at a real LLM

### OpenAI

```bash
export OPENAI_API_KEY=sk-...
export TRUSTLENS_BACKEND_URL=https://api.openai.com/v1
trustlens serve-gateway --port 8081
```

Now pass `"model": "gpt-4o"` (or any OpenAI model) in your request.

### Anthropic

```bash
pip install -e '.[anthropic]'
export ANTHROPIC_API_KEY=sk-ant-...
trustlens serve-gateway --port 8081
```

Request body: `"model": "claude-3-7-sonnet-20250219"`.

### Ollama (local)

```bash
export OLLAMA_BASE_URL=http://localhost:11434
trustlens serve-gateway --port 8081
```

Request body: `"model": "llama3.1:8b"`.

### vLLM / Together / anything OpenAI-compatible

```bash
export TRUSTLENS_BACKEND_URL=http://localhost:8000/v1      # vLLM
# or https://api.together.xyz/v1 with OPENAI_API_KEY=<together-key>
trustlens serve-gateway --port 8081
```

## 7. Load your own knowledge base

Claims will ground against whatever documents are in the KB. Load them:

```bash
curl -X POST http://localhost:8081/v1/kb/load \
  -H "Content-Type: application/json" \
  -d '{
        "tenant_id": "acme",
        "documents": [
          {"doc_id": "pol-001", "text": "Refunds are issued within 14 days of purchase.", "source_uri": "kb://pol-001"},
          {"doc_id": "pol-002", "text": "Our SLA is 99.9% uptime for paid tiers."}
        ]
      }'
```

Check status:

```bash
curl http://localhost:8081/v1/kb/status?tenant_id=acme
```

When a model answers a question whose claims match your docs, the certificate
will show `verdict: "verified"` per claim, with oracle receipts citing the
matched `doc_id` and `source_uri`.

## 8. Pick a verification tier per request

Add `trustlens.verification_tier` to the request body:

```json
{
  "model": "gpt-4o",
  "messages": [ ... ],
  "trustlens": {
    "verification_tier": "deep"
  }
}
```

Three tiers:

| Tier      | What runs                              | Budget   | When to use |
|-----------|----------------------------------------|----------|-------------|
| `fast`    | NLI only, no oracle calls              | <30 ms   | Streaming first-token optimization |
| `standard`| NLI + your KB oracles                  | <100 ms  | Default production |
| `deep`    | NLI + KB + Wikidata + Deep Inspector  | <500 ms  | High-stakes / compliance / agentic |

## 9. Where to go next

- **[INTEGRATION.md](INTEGRATION.md)** — wire up your production KB, auth,
  per-tenant SLAs, custom oracles
- **[BENCHMARKS.md](BENCHMARKS.md)** — the 5-suite benchmark harness, how to
  reproduce every number in the README
- **[OPERATIONS.md](OPERATIONS.md)** — Prometheus dashboards, error budgets,
  runbooks, dead-letter queue handling
- **[ENTERPRISE.md](ENTERPRISE.md)** — what's production-ready today, what's
  on the roadmap, compliance posture
