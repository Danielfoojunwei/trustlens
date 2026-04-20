# OPERATIONS — SRE guide

Runbooks, SLOs, metrics, and incident response for TrustLens in production.

## Contents

1. [Service architecture](#service-architecture)
2. [SLOs](#slos)
3. [Metrics & dashboards](#metrics--dashboards)
4. [Logs](#logs)
5. [Tracing](#tracing)
6. [Common incidents](#common-incidents)
7. [Key rotation runbook](#key-rotation-runbook)
8. [Benchmarking standards](#benchmarking-standards)
9. [Capacity planning](#capacity-planning)

---

## Service architecture

TrustLens ships as a single Python process that exposes HTTP endpoints.
The recommended production topology:

```
                            ┌──────────────┐
   ALB / ingress  ─► TLS ─► │  TrustLens   │ ─► backend LLM (OpenAI/Anthropic/vLLM)
                            │   gateway    │
                            │  (replica N) │
                            └──────┬───────┘
                                   │
                   ┌───────────────┼────────────────┐
                   ▼               ▼                ▼
               Redis         Postgres           Object store
            (budgets)      (tenant config)     (cert archive)
```

- **Replicas**: stateless; scale horizontally behind the ingress.
- **Redis**: stores rate-limit budgets so replicas share state.
  (If running single-replica, the in-memory budget is fine.)
- **Postgres**: authoritative tenant config store.
- **Object store**: S3/GCS/Azure Blob for long-term cert retention.

## SLOs

Target SLOs, measured monthly:

| SLO | Target | Error budget |
|-----|--------|--------------|
| Gateway availability | 99.95% | 21.6 min / 30d |
| Verification success rate | 99.9% | 43.2 min / 30d |
| Verify p99 latency (LEXICAL tier) | ≤ 50 ms | n/a |
| Verify p99 latency (STANDARD tier) | ≤ 150 ms | n/a |
| Verify p99 latency (DEEP tier) | ≤ 500 ms | n/a |
| Certificate signing p99 | ≤ 5 ms | n/a |
| Offline cert verification p99 | ≤ 50 ms | n/a |

*"Verification success"* means the gateway returned a valid response with a
signed cert — NOT that the cert status was `VERIFIED`. A `BLOCKED`
response with a valid signature counts as success.

## Metrics & dashboards

All metrics are on `GET /metrics`. Recommended Grafana panels:

### Top-level (one dashboard, all tenants)

1. **Request rate** — `sum by (status) (rate(trustlens_requests_total[1m]))`
2. **Verification p99** — `histogram_quantile(0.99, sum by (le,tier) (rate(trustlens_verify_duration_seconds_bucket[5m])))`
3. **Cert status mix** — `sum by (status) (rate(trustlens_certificate_status_total[5m]))`
4. **Budget rejections** — `sum by (kind) (rate(trustlens_budget_rejections_total[5m]))`
5. **Circuit breakers** — `trustlens_circuit_breaker_state` (gauge per backend)
6. **Oracle health** — `sum by (oracle,reason) (rate(trustlens_oracle_failures_total[5m]))`
7. **Backend latency** — `histogram_quantile(0.95, rate(trustlens_backend_latency_seconds_bucket[5m]))`

### Per-tenant (one dashboard per paying customer)

Filter every panel by `{tenant="acme"}`. Add:

- **Token consumption** — from `trustlens_tokens_consumed_total`
- **Blocked fraction** — the ratio of BLOCKED / total cert statuses
- **Oracle hit rate** — matched KB docs / total queries

### Alerts

```yaml
- alert: TrustLensHighErrorRate
  expr: |
    sum by (tenant) (rate(trustlens_requests_total{status=~"5.."}[5m]))
    / sum by (tenant) (rate(trustlens_requests_total[5m])) > 0.01
  for: 5m

- alert: TrustLensVerifyP99High
  expr: |
    histogram_quantile(0.99,
      sum by (le,tier) (rate(trustlens_verify_duration_seconds_bucket[5m]))
    ) > 0.5
  for: 10m

- alert: TrustLensCircuitBreakerOpen
  expr: trustlens_circuit_breaker_state > 0
  for: 2m

- alert: TrustLensOracleFailures
  expr: |
    sum by (oracle) (rate(trustlens_oracle_failures_total[5m])) > 1
  for: 5m
```

## Logs

TrustLens does not enforce a log format — it uses Python's stdlib `logging`.
For production, configure JSON logs in your app bootstrap:

```python
import logging, json, sys
logging.basicConfig(
    stream=sys.stdout,
    format=json.dumps({"ts": "%(asctime)s", "lvl": "%(levelname)s",
                       "logger": "%(name)s", "msg": "%(message)s"}),
    level=logging.INFO,
)
```

Correlation: every certificate ID appears in logs at its signing point and
can be grepped end-to-end.

## Tracing

Install `trustlens[otel]` and set:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.infra:4317
OTEL_SERVICE_NAME=trustlens-gateway
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod
```

Emitted spans:

| Span | Attributes | When |
|------|------------|------|
| `gateway.chat_completion` | tenant, backend, model, tier | every request |
| `verifier.extract` | n_claims | once per request |
| `verifier.oracle.<name>` | latency_ms, support, evidence_len | per oracle call |
| `verifier.nli.<impl>` | verdict, confidence | per claim |
| `certificate.sign` | key_id, cert_id | once per request |
| `backend.<name>` | model, status, tokens | per backend call |

## Common incidents

### Incident: verify p99 spiked past SLO

Symptoms: `TrustLensVerifyP99High` alert firing; customers reporting timeouts.

Checks:

1. Which tier? — split by `{tier=...}` in the p99 panel. If only DEEP,
   it's likely transformer NLI GPU contention.
2. Which tenant? — if localized, check `trustlens_budget_rejections_total` —
   they may be hitting their budget and retrying.
3. Which oracle? — `rate(trustlens_oracle_failures_total[5m])` + backend
   latency. Wikidata SPARQL can spike; the circuit breaker should trip.

Remediation:

- Drop temporarily: `TenantConfig.verify_deadline_ms` can be lowered
  without a redeploy if tenant config store is Postgres-backed.
- Skip Wikidata: remove from `TenantConfig.allowed_oracles`.
- Fall back to STANDARD tier if DEEP is degraded — the gateway honors
  per-request overrides.

### Incident: all requests returning `cert_status: blocked`

Symptoms: cert_status mix suddenly skews to BLOCKED; customers see blanked
content.

Checks:

1. KB ingestion — was there a `/v1/kb/load` call that accidentally added
   contradicting docs? Check `trustlens_kb_admin_loaded_total` increments.
2. NLI upgrade — was a transformer NLI rolled out? Look at the pinned
   `pipeline_version` in certificates; a version bump is the trigger.
3. Oracle poisoning — query the KB for a known-good claim, read the
   `OracleReceipt.source_uri` in the cert.

Remediation:

- Roll back pipeline_version with a redeploy.
- Purge bad docs with a tenant-scoped KB reload.
- Increase `tau` temporarily in the tenant config if the NLI is too strict.

### Incident: circuit breaker stuck OPEN on a backend

Symptoms: `TrustLensCircuitBreakerOpen` firing; all requests to backend X
returning 503 "no backend available".

Checks:

1. Upstream health — curl the backend directly from a gateway pod.
2. Credential rotation — did the OpenAI/Anthropic API key change?
3. Rate-limit response — backends may be throttling the whole organization.

Remediation:

- Breaker recovers automatically after `recovery_time_s` (default 30 s) +
  one probe success. If you need to force it, restart the replica.
- Temporarily route tenants to a different backend via
  `TenantConfig.allowed_backends`.

## Key rotation runbook

Cadence: **annually at minimum**, or immediately on compromise.

1. Generate new key: `trustlens keygen --out ./.trustlens/signer-new.pem`
2. Deploy a config with **both** keys loaded; sign with the new one, accept
   both on the verifier side (`--trusted-key-ids old,new`).
3. Wait out the certificate retention window (see SLOs — 90 days standard).
4. Remove the old key from the accepted-list.
5. Retain the old public key in your KMS *indefinitely* for historical
   verification.

## Benchmarking standards

Any time the team publishes a performance claim about TrustLens, it must
satisfy:

1. **Measured not projected.** Numbers come from a real run, not arithmetic.
2. **Labeled methodology.** What tier? What KB? What dataset? What tau?
3. **Reproducible.** The exact command to reproduce is in the release notes.
4. **Signed.** Benchmark scorecards are Ed25519-signed via `sign_scorecard()`.
5. **3+ iterations.** Report mean ± std for latency. Single-run numbers
   are labeled "spot check" and not used in marketing.

Bench regression guard: CI runs the 5-suite benchmark on every PR.
A merge that flips any suite from PASS to FAIL is blocked.

## Capacity planning

Rough sizing guide, based on measured performance:

| Load | Replicas | Memory | CPU |
|---|---|---|---|
| ≤ 100 RPS, LEXICAL tier | 2 | 512 MB | 1 core |
| ≤ 1000 RPS, LEXICAL tier | 4 | 1 GB | 2 cores |
| ≤ 100 RPS, DEEP tier (with transformer NLI on CPU) | 4 | 4 GB | 4 cores |
| ≤ 100 RPS, DEEP tier (GPU) | 2 | 8 GB + 1 GPU | 2 cores |

These are starting points. Run the load test in `tests/` against a
representative corpus before committing to capacity.
