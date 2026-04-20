---
name: trustlens-ops
description: Day-2 operations — health checks, metrics, users + API keys, integrations, tuning settings. Use whenever the user asks "is the gateway ok", wants to manage operators, mint/revoke an API key, toggle an integration (Wikidata, OpenAI, Anthropic, Slack alerts), or read Prometheus metrics.
---

# TrustLens day-2 ops

## Health + readiness

- `trustlens_version()` — also returns `/healthz` blob.
- For deeper diagnostics, instruct the user to run `trustlens doctor`
  in their shell (returns full structured JSON).

## Users + roles

The 4 built-in roles (read-only descriptions for the user):
- **owner** — every permission, including user management
- **admin** — read+write everything except user management
- **operator** — playground, KB, incident ack
- **viewer** — read-only

`users_list()` to list. `user_create(email, display_name, role, password)`
to create. Role rules:
- For SSO/OIDC accounts, omit `password` (the OIDC provider is the
  source of truth).
- `password` is hashed with PBKDF2 server-side; don't pass production
  passwords through chat — direct the user to set them via the
  dashboard or the OIDC redirect flow.

## API keys

`keys_list()` to list. `key_mint(name, tenant_id, role)` returns a
**plaintext secret only once** — relay it to the user immediately and
remind them to store it securely.

`key_revoke(key_id)` is destructive — confirm first.

## Integrations

`integrations_list()` returns the catalog (oracle.wikidata,
oracle.customer_kb, llm.openai/anthropic/ollama, alerts.webhook,
alerts.slack, alerts.pagerduty, obs.otel, auth.oidc, vector.{pinecone,pgvector,qdrant}).

`integration_set(kind, enabled, name, config)` upserts. Examples:

```python
# Enable Wikidata oracle
integration_set("oracle.wikidata", True,
                config={"endpoint": "https://query.wikidata.org/sparql"})

# Wire Slack incident alerts
integration_set("alerts.slack", True,
                config={"url": "https://hooks.slack.com/services/..."})
```

Always confirm webhook URLs with the user before saving — they often
contain secrets.

## Settings (verifier knobs)

`settings_get()` shows all live feature flags. `settings_update(...)`
patches. The DEEP-tier knobs (ssh_threshold_rho, ssh_compute_every_n,
steering_alpha, steering_top_k_layers) belong to the
`trustlens-deep-inspector` skill — defer there if the conversation
goes that direction.

## Metrics (Prometheus)

The gateway exposes plaintext at `GET /metrics`. The agent should
direct the user at Grafana queries from `docs/OPERATIONS.md` rather
than dumping raw Prometheus into chat. Common spot-checks:

- `requests/sec by status`: `sum by (status) (rate(trustlens_requests_total[1m]))`
- `verify p99`: `histogram_quantile(0.99, sum by (le,tier) (rate(trustlens_verify_duration_seconds_bucket[5m])))`
- `cert status mix`: `sum by (status) (rate(trustlens_certificate_status_total[5m]))`

## Common day-2 tasks

- "Add a new admin" → `user_create(...)` then ask the user to set the
  real password via the dashboard.
- "We're getting too many SSH alarms" → `incidents_list(kind="ssh.critical")`
  to count → `settings_update(ssh_threshold_rho=0.98)` to loosen.
- "Wire alerts to Slack" → `integration_set("alerts.slack", True, config={"url":"..."})`.
  Then trigger a test incident to confirm: open a low-severity breach
  and verify it shows up in Slack.
- "Rotate the signer key" → out-of-band CLI: `trustlens keygen --out
  ./.trustlens/signer-new.pem`. Run both keys in parallel for the
  rotation window. Verifiers accept multiple `--trusted-key-ids`.
