# INTEGRATION — enterprise wiring

This guide shows how to bolt TrustLens onto an existing production LLM stack.
Start with `docs/QUICKSTART.md` if you haven't seen a certificate flow yet.

## Contents

1. [Deployment shapes](#deployment-shapes)
2. [Backends](#backends)
3. [Tenant configuration](#tenant-configuration)
4. [Bring your own knowledge base](#bring-your-own-knowledge-base)
5. [Custom oracles](#custom-oracles)
6. [Verification tier policy](#verification-tier-policy)
7. [Streaming integration](#streaming-integration)
8. [Client SDK & offline verification](#client-sdk--offline-verification)
9. [Metrics, tracing, shadow eval](#metrics-tracing-shadow-eval)
10. [Key rotation & cert retention](#key-rotation--cert-retention)

---

## Deployment shapes

TrustLens is one Python package. The three common deployment shapes:

### 1. Reverse proxy (recommended)

```
   app ──► TrustLens gateway (8081) ──► upstream LLM
```

Your application points at the TrustLens gateway; TrustLens proxies to the
real LLM. Add the gateway behind your ingress (ALB, NGINX, Envoy). No app
changes beyond a base URL swap.

### 2. Embedded verifier

```
   app ──► upstream LLM
     └──► TrustLens /v1/verify   (out-of-band)
```

Keep your direct-to-LLM path; POST `{prompt, response, kb_hits?}` to the
TrustLens verifier service. Use this when you can't move your traffic through
a proxy (e.g., strict vendor-lock architecture) but still want signed certs.

### 3. SDK-embedded (single-process)

```python
from trustlens.verifier.engine import VerifierEngine, VerificationRequest

engine = VerifierEngine(oracle_registry, nli=NumericAwareNLI(inner=SpanAwareNLI()))
result = await engine.verify(VerificationRequest(...))
```

Library use inside your own service. You still own signing and cert storage.

## Backends

Set environment variables before `trustlens serve-gateway`:

```bash
# OpenAI or any OpenAI-compatible endpoint
export OPENAI_API_KEY=sk-...
export TRUSTLENS_BACKEND_URL=https://api.openai.com/v1

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
# (install the extras: pip install -e '.[anthropic]')

# Ollama
export OLLAMA_BASE_URL=http://ollama.infra.svc.cluster.local:11434
```

You can register several backends at once — models are routed by the `model`
string in the request. Only backends listed in `TenantConfig.allowed_backends`
are reachable for each tenant.

To add a new backend, implement the `Backend` protocol in
`trustlens/gateway/backends.py`:

```python
class MyBackend:
    name = "mybackend"
    async def complete(self, req: ChatCompletionRequest) -> BackendResponse: ...
    async def stream(self, req: ChatCompletionRequest) -> AsyncIterator[BackendStreamChunk]: ...
    async def close(self) -> None: ...
```

Register it with `BackendRegistry([MyBackend(), ...])` in your bootstrap code.

## Tenant configuration

TrustLens is multi-tenant from day one. A `TenantConfig` carries:

| Field                   | Meaning                                         |
|-------------------------|-------------------------------------------------|
| `tier`                  | FREE / PRO / ENTERPRISE / DEEP_INSPECTOR        |
| `tau`                   | verdict threshold for `VERIFIED`                |
| `tau_prime`             | verdict threshold for `CONTRADICTED`            |
| `max_rps`               | token-bucket request rate                       |
| `max_tokens_per_minute` | sliding-window token budget                     |
| `allowed_backends`      | whitelist of backend names                      |
| `allowed_oracles`       | whitelist of oracle names                       |
| `verify_deadline_ms`    | hard upper bound on verification wall time      |

In-memory demo:

```python
from trustlens.tenancy.config import InMemoryTenantStore, TenantConfig, TenantTier

store = InMemoryTenantStore([
    TenantConfig(tenant_id="acme", tier=TenantTier.ENTERPRISE,
                 tau=0.40, tau_prime=0.10,
                 max_rps=200, max_tokens_per_minute=1_500_000,
                 allowed_backends=["openai", "anthropic"],
                 allowed_oracles=["customer_kb", "wikidata"],
                 verify_deadline_ms=500),
])
```

For production, implement `TenantConfigStore` against your config source —
Consul, etcd, Postgres, or a SaaS control-plane API.

## Bring your own knowledge base

### Option 1 — lexical (TF-IDF, dependency-free)

Good for small corpora (≤10k short docs), offline dev, regression tests.

```python
from trustlens.oracles.customer_kb import LexicalKBIndex, KBDocument, CustomerKBOracle

kb = LexicalKBIndex()
for doc in load_my_docs():
    kb.add(KBDocument(doc_id=doc.id, text=doc.text, source_uri=doc.uri),
           tenant_id="acme")

oracle = CustomerKBOracle(kb, name="customer_kb")
```

### Option 2 — dense (sentence-transformers)

```bash
pip install -e '.[nli]'
```

```python
from trustlens.oracles.vector_kb import VectorKBIndex
from trustlens.oracles.customer_kb import CustomerKBOracle, KBDocument

kb = VectorKBIndex(model_name="sentence-transformers/all-MiniLM-L6-v2")
for doc in load_my_docs():
    kb.add(KBDocument(doc_id=doc.id, text=doc.text), tenant_id="acme")

oracle = CustomerKBOracle(kb, name="customer_kb_vector")
```

### Option 3 — your production vector DB

The `VectorIndex` protocol is two methods:

```python
class VectorIndex(Protocol):
    async def search(
        self, query: str, tenant_id: Optional[str], top_k: int
    ) -> list[tuple[KBDocument, float]]: ...
```

Implement it against Pinecone, pgvector, Qdrant, Weaviate, OpenSearch, or your
internal search service. `CustomerKBOracle` wraps any implementer.

### Bulk-load via the admin API

```bash
curl -X POST http://localhost:8081/v1/kb/load \
  -H "Content-Type: application/json" \
  -d @my-docs.json
```

where `my-docs.json` is:

```json
{
  "tenant_id": "acme",
  "documents": [
    {"doc_id": "pol-001", "text": "...", "source_uri": "kb://pol-001"}
  ]
}
```

Protect `/v1/kb/*` behind an admin auth layer in your ingress (separate API
key, IP allow-list, mTLS).

## Custom oracles

An oracle turns a claim into evidence + confidence. Implement the `Oracle`
protocol:

```python
from trustlens.oracles.base import Oracle, OracleQuery, OracleResponse

class MyInternalAPIOracle:
    name = "acme_policy_api"
    async def lookup(self, query: OracleQuery) -> OracleResponse:
        hits = await my_policy_client.search(query.claim_text)
        return OracleResponse(
            oracle_name=self.name,
            evidence="\n".join(h.text for h in hits[:3]),
            support=min(hits[0].score, 0.95) if hits else 0.0,
            contradiction=0.0,
            source_uri=hits[0].url if hits else None,
            # ...
        )
    async def close(self): ...
```

Wrap with `NegationAwareOracle` for free contradiction handling, then register:

```python
from trustlens.oracles.registry import OracleRegistry
from trustlens.oracles.negation_aware import NegationAwareOracle

registry = OracleRegistry([
    NegationAwareOracle(inner=CustomerKBOracle(kb), name="customer_kb"),
    NegationAwareOracle(inner=MyInternalAPIOracle(), name="acme_policy_api"),
    WikidataOracle(),
])
```

## Verification tier policy

Per-request override:

```json
{"trustlens": {"verification_tier": "deep"}}
```

Per-tenant policy is set by `TenantConfig.verify_deadline_ms` which caps the
tier's deadline. The resolver in `trustlens/gateway/verification_tier.py`
lives alone — add a new tier by extending the `VerificationTier` enum and
adding a branch to `resolve_tier()`.

## Streaming integration

The gateway supports SSE streaming. Request:

```json
{"model": "gpt-4o", "messages": [...], "stream": true}
```

Streaming response: normal OpenAI SSE with a terminal `trustlens` chunk
containing the certificate annotation. The certificate is *minted after*
the final token because verification is streaming-unsafe in the general
case (claims may span chunks).

If you need token-level blocking (kill the stream early on a severe
contradiction), use the `deep` tier + `masked_claim_ids` logic — the
gateway can replace a masked claim's content with `[REDACTED: claim-N]`
before the terminal chunk reaches the client.

## Client SDK & offline verification

Python:

```python
from trustlens import TrustLens

client = TrustLens(base_url="https://trustlens.yourco.net",
                   tenant_id="acme")
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "When was X founded?"}],
)
print(resp.content, resp.certificate_id, resp.certificate_status)
```

Offline verification (e.g., in an auditor's sandbox):

```python
from trustlens.sdk.verify_cert import verify_certificate_file

ok = verify_certificate_file(
    cert_path="/audit/certs/abc123.json",
    public_key_pem_path="/audit/keys/signer.pub.pem",
)
```

or via CLI:

```bash
trustlens verify /audit/certs/abc123.json \
  --public-key /audit/keys/signer.pub.pem \
  --require-pipeline-version pipeline/1.0.0
```

## Metrics, tracing, shadow eval

### Prometheus

`GET /metrics` exposes:

- `trustlens_requests_total{tenant,backend,status}`
- `trustlens_verify_duration_seconds{tenant,tier,oracle}` (histogram)
- `trustlens_certificate_status_total{status}` (VERIFIED/PARTIAL/BLOCKED/DEGRADED)
- `trustlens_backend_latency_seconds{backend}` (histogram)
- `trustlens_budget_rejections_total{tenant,kind}` (rps/tokens)
- `trustlens_circuit_breaker_state{backend}`
- `trustlens_oracle_failures_total{oracle,reason}`

Histogram buckets are SLA-tuned (median <10 ms, p99 <500 ms).

### OpenTelemetry

```bash
pip install -e '.[otel]'
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.infra:4317
export OTEL_SERVICE_NAME=trustlens-gateway
```

Spans emitted: `gateway.chat_completion`, `verifier.extract`,
`verifier.oracle.<name>`, `verifier.nli.<impl>`, `certificate.sign`.

### Shadow eval

The `ShadowEvalSampler` (in `trustlens/robustness/shadow_eval.py`) samples
a deterministic fraction of production traffic, runs it through a shadow
verifier config (e.g., a new NLI model), and writes diffs to a JSONL file.
Use this to validate a verifier upgrade on live traffic before cutting over.

## Key rotation & cert retention

- **Signer keys:** generate a new keypair, run both signers in parallel for a
  rotation window, then retire the old private key. Verifiers accept a set of
  trusted key IDs via `trustlens verify --trusted-key-ids old-key,new-key`.
- **Cert store:** the default `FilesystemStore` is content-addressed. In
  production, write to S3 / GCS with a retention policy matching your
  compliance regime (7 years for financial, variable for healthcare).
- **Pipeline version:** every cert carries `pipeline_version`. Auditors
  verify against the pinned version from the time the response was issued,
  not the current running version. Bump the version on any verifier logic
  change.
