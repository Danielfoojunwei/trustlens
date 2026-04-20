# ENTERPRISE — production-readiness checklist & roadmap

This document is the honest state of TrustLens for enterprise deployment today.
No marketing copy. Items marked **SHIP** are in the codebase now; items marked
**NEAR** are on the immediate roadmap; items marked **NEXT** are planned.

## Contents

1. [What works today (SHIP)](#what-works-today-ship)
2. [Missing for enterprise sale (NEAR)](#missing-for-enterprise-sale-near)
3. [On the roadmap (NEXT)](#on-the-roadmap-next)
4. [Compliance posture](#compliance-posture)
5. [Security model](#security-model)
6. [Supported integrations](#supported-integrations)
7. [Commercial readiness checklist](#commercial-readiness-checklist)

---

## What works today (SHIP)

The following are in the codebase, covered by tests, and pass all 5 SLA
benchmark suites on the `lexical` tier:

**Gateway**
- OpenAI-compatible `/v1/chat/completions` (buffered + SSE streaming)
- Backends: OpenAI-compatible, Anthropic, Ollama, Echo (dev)
- Per-tenant routing, circuit breakers, token-bucket budgets, deadlines
- Per-request VerificationTier (FAST / STANDARD / DEEP)
- Prometheus `/metrics`, optional OpenTelemetry spans

**Verifier**
- Compositional claim DAG with anaphora edges + cycle detection
- NLI stack: lexical → span-aware → numeric-aware → (optional) DeBERTa-v3
- Sycophancy detector: leading-cue + counterfactual divergence
- Platt scaling calibration + ECE/MCE/Brier

**Oracles**
- Customer KB: lexical TF-IDF index *and* sentence-transformer vector index
- Wikidata SPARQL oracle
- Negation-aware wrapper (composes with any oracle)
- TTL cache
- Pluggable `Oracle` + `VectorIndex` protocols for custom backends

**Certificates**
- Ed25519 signing, content-addressed
- Offline CLI verification with pipeline-version pinning + key rotation
- Filesystem cert store (in-memory for tests)

**Deep Inspector**
- SSH (spectral stability hooks) — real power-iteration adapter for HF models
- Activation steering — real forward-hook adapter (Llama/Mistral/GPT/OPT)
- TrustChain agentic cascade detection
- 5-suite benchmark harness with signed scorecards

**CLI**
- `trustlens keygen | verify | inspect | serve-verifier | serve-gateway`
- `trustlens calibrate | attribution | sweep`

**Ops**
- Multi-stage Dockerfile (optional `WITH_TRANSFORMERS=1` CUDA layer)
- 76 passing tests
- Failure attribution & 10-axis capability sweep

## Missing for enterprise sale (NEAR)

These are the gaps a Fortune-500 procurement cycle typically asks about. The
codebase is small enough that each item is a single-PR scope.

### 1. Auth & identity

| Gap | Current | What to add |
|-----|---------|-------------|
| API-key auth | `X-TrustLens-Tenant-Id` header (no secret) | Per-tenant HMAC API keys in an `ApiKeyStore`, rotate via admin API |
| SSO | — | OIDC/SAML provider plug-in on admin endpoints |
| mTLS | — | Document ingress-level mTLS + cert-CN-to-tenant mapping |
| Admin RBAC | — | Role-gated `/v1/kb/*`, `/v1/admin/*` — separate keys from data-plane |

### 2. Persistent state

| Gap | Current | What to add |
|-----|---------|-------------|
| Tenant config | `InMemoryTenantStore` | Postgres / Consul `TenantConfigStore` adapter |
| Budget ledger | In-memory | Redis-backed token-bucket (`RedisBudgetTracker`) for cluster HA |
| Cert store | Filesystem | S3 / GCS / Azure Blob `ObjectStore` adapters |
| KB index | In-memory | Pinecone / pgvector / Qdrant `VectorIndex` adapters |

### 3. Production vector DB adapters

`VectorIndex` protocol exists; concrete adapters do not yet. Priority order
based on install base:

1. `PineconeIndex` (SaaS)
2. `PgvectorIndex` (Postgres-native, easiest to audit)
3. `QdrantIndex`
4. `WeaviateIndex`
5. `OpenSearchIndex`

### 4. Deployment artifacts

| Gap | Current | What to add |
|-----|---------|-------------|
| Kubernetes | — | Helm chart with HPA, PDBs, PodSecurityPolicy |
| Terraform | — | Modules for AWS (ECS/EKS), GCP (GKE), Azure (AKS) |
| Compose stack | — | `docker-compose.yml` with Redis + Postgres + Prom + Grafana |

### 5. Admin UI

Nothing today. A minimal Next.js operator console showing cert stream,
tenant budgets, circuit-breaker states, and KB ingest progress is worth a
week of work and removes a whole class of sales objections.

### 6. Billing / metering

The metrics already exist — what's missing is the usage export:

- Periodic `usage_records.jsonl` export per tenant
- Marketplace integrations: AWS Marketplace metering API, GCP Marketplace
- Stripe metering for self-serve

### 7. SDK language coverage

- Python SDK: SHIP
- TypeScript SDK: needed for Node/Vercel apps (same surface as `openai-node`)
- Go SDK: needed for enterprise backend teams
- Java SDK: needed for banking/insurance accounts

### 8. Red-team suite

The `deep_inspector/benchmarks/` suites cover factuality and chain
cascades. Missing: a **jailbreak / prompt-injection** suite drawn from
HarmBench, AdvBench, and the authors' own held-out corpus. Publish the
pass rates with the next release.

### 9. Content-safety policy layer

Hallucination ≠ safety. An enterprise will ask: *"Does this also stop the
model from outputting PII / hate speech / regulated content?"* Today the
answer is *"that's out of scope; compose a classifier upstream."* NEAR-term:
ship a `PolicyGuard` middleware that runs a list of regex/classifier policies
against the response and surfaces the result alongside the claim-level
verdict in the certificate.

### 10. Documented SLOs & status page

`docs/OPERATIONS.md` already defines the SLO targets. What's missing is
a public status page, error-budget tracking wired into Prometheus/Grafana,
and a burn-rate alert runbook.

## On the roadmap (NEXT)

These are larger-scope items that unlock new segments:

1. **PII redaction** — structured PII detection + cert-level attestation
   of what was redacted.
2. **Claim provenance chains** — when a claim fans out to N oracles,
   preserve the full provenance in the cert (not just the winning oracle).
3. **Delta certificates** — for long streaming completions, issue
   incremental certs rather than one terminal cert.
4. **Canary verification** — run a percentage of traffic through a
   shadow verifier config and flag regressions automatically.
5. **Native Rust oracle runtime** — the lexical KB + NLI can run at
   single-digit µs latency in Rust; worth it for very high QPS tenants.
6. **Federated KBs** — query customer KB *and* partner KBs with
   cross-tenant access control in a single verification.

## Compliance posture

| Regime | Current posture | Work required |
|---|---|---|
| SOC 2 Type II | No attestation | 6-month observation window; plumbing (audit logs, immutable cert store, access-review cadence) is close |
| ISO 27001 | No attestation | Same observation window; requires formal ISMS |
| HIPAA | Architecture compatible (BYO-KMS, PHI boundary at the customer KB) | BAA template + encryption-at-rest story |
| GDPR | Compatible (no PII exfiltration by design) | DPA template + data-residency controls |
| EU AI Act (high-risk) | Compatible (signed certs satisfy traceability requirement) | Formal Article 13 technical documentation |
| FedRAMP Moderate | Not compatible today | Requires GovCloud deployment + FIPS-validated crypto (`cryptography` lib is in progress) |

## Security model

**Threat model — assumed:**
- An adversary controls the prompt (prompt injection).
- An adversary controls one or more KB documents (poisoning).
- An adversary has network access between app and gateway (MITM).
- An adversary cannot forge Ed25519 signatures without the private key.

**What TrustLens defends:**
- Prompt injection of the form "ignore prior instructions" is caught at the
  *claim* level: injected instructions don't produce claims that ground
  against the legitimate KB, so they're UNCERTAIN/UNSUPPORTED.
- KB poisoning of a single document is detected via per-doc NLI disagreement
  (the negation-aware oracle).
- MITM is defended by running TrustLens behind TLS *and* by the certificate
  itself — a downstream app that verifies the cert's signature against the
  signer's public key can detect tampering.

**What TrustLens does not defend (out of scope, compose upstream):**
- Malicious user accounts / abuse at the application layer.
- PII leakage from the underlying LLM (compose a PII classifier).
- Latent training-data memorization (compose a DMCA/copyright filter).

## Supported integrations

**LLM providers:** OpenAI, Anthropic, Ollama, any OpenAI-compatible endpoint
(vLLM, Together, Groq, Fireworks, Databricks, Azure OpenAI).

**Observability:** Prometheus, OpenTelemetry (any OTLP collector).

**KBs / retrieval:** lexical TF-IDF (bundled), sentence-transformers (bundled
via `[nli]` extras). Vector DB adapters NEAR-term.

**Crypto:** `cryptography` (Ed25519). All signing/verification uses the
standard library `cryptography` module — no hand-rolled crypto.

## Commercial readiness checklist

Use this as the gate before a paying pilot:

- [ ] At least one production vector DB adapter shipped (pgvector is the
  fastest path)
- [ ] Redis-backed budget tracker
- [ ] Postgres-backed tenant config
- [ ] Helm chart + Terraform module published
- [ ] API-key auth middleware + admin RBAC
- [ ] docker-compose stack for local dev parity
- [ ] SDK for TypeScript + Go
- [ ] Red-team suite published with pass rates
- [ ] Admin UI MVP
- [ ] Legal: Apache-2.0 ✓ (done); DPA + BAA templates drafted
- [ ] SOC 2 readiness assessment scheduled
- [ ] Documented SLO + public status page
- [ ] Case study with a design-partner customer
