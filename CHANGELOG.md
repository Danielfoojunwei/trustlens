# Changelog

All notable changes to TrustLens are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Auth guards on every `/v1/admin/*` endpoint (admin API, events stream,
  analytics, deep health, certs, tenants).
- `POST /v1/verify` server-side certificate verification endpoint.
- TLS support via `--ssl-keyfile` / `--ssl-certfile` CLI flags on
  `serve-gateway` and `serve-verifier`. `TRUSTLENS_PROD_MODE=1` refuses to
  start the gateway without TLS (or `TRUSTLENS_BEHIND_TLS_PROXY=1`).
- FastAPI lifespan that drains backends on shutdown; `SIGTERM`-safe.
- Per-IP token-bucket rate limit (`trustlens.gateway.ratelimit.PerIPRateLimit`).
- CORS middleware (opt-in via `cors_origins=` argument or
  `TRUSTLENS_CORS_ORIGINS` env var).
- Request body size limit middleware (`max_request_bytes`, default 2MB).
- Exponential-backoff retry for `OpenAICompatBackend` on connect/read
  timeouts and 429/5xx responses.
- Non-blocking audit log writes (`asyncio.to_thread`) on the gateway hot
  path.
- File-backed `SettingsStore` (atomic JSON persistence) in
  `trustlens.gateway.ops_routes`.
- Kubernetes deployment manifest in `deploy/kubernetes/`.
- `trustlens.gateway.app.build_gateway_from_env` factory for uvicorn
  `--factory` multi-worker deployments.
- Dockerfile `HEALTHCHECK`, multi-worker CMD, and `TRUSTLENS_WORKERS` env
  var.
- Agent control surface (`trustlens.gateway.agent_routes`) — read-only +
  mutation endpoints an agentic harness can call on the user's behalf.
- `SKILL.md` at the repo root describing how an agent harness can manage a
  TrustLens deployment end-to-end (setup, monitor, schedule, alert).
- Utility modules `trustlens.utils.redact` (secret redaction for logs) and
  `trustlens.utils.crypto` (shared SHA-256 / ISO-8601 helpers).
- Ruff lint/format config in `pyproject.toml`.
- GitHub Actions CI workflow (`.github/workflows/ci.yml`) running the full
  suite on Python 3.10 / 3.11 / 3.12.
- Tests covering auth, RBAC, admin API 401/403 gating, `/v1/verify`, body
  size limit, and CORS.

### Changed
- `bootstrap_default_users()` no longer hardcodes weak viewer/operator
  passwords. It requires `TRUSTLENS_BOOTSTRAP_EMAIL` and
  `TRUSTLENS_BOOTSTRAP_PASSWORD` to seed an owner; with
  `TRUSTLENS_PROD_MODE=1` the gateway refuses to start if they are missing.
- `/healthz` returns a per-dependency `checks` map and answers 503 when a
  dependency fails. `/readyz` returns 503 if no backends are registered.
- Silent `except Exception: pass` blocks around audit-log, axis log, and
  cert-store writes now log at `error` / `warning` level; behaviour is
  unchanged from the caller's perspective.
- Secret values seen in provider exception strings are masked via
  `redact_secrets` before being written to logs or returned to clients.
- `InMemoryAuditLog.append()` is still synchronous (the chain is
  single-threaded), but gateway callers now wrap it in `asyncio.to_thread`.

### Security
- Removed hardcoded default bootstrap credentials for viewer/operator
  accounts.
- All privileged admin endpoints now return 401 for unauthenticated callers
  and 403 for callers lacking the relevant RBAC permission.
- `_CTX` (auth context singleton) is now lock-guarded.

## [1.0.0] — 2026-01-15

Initial release. Signed trust certificates, gateway, verifier,
claim DAG, Wikidata and customer-KB oracles, Deep Inspector (SSH / RAD-CoT
/ chain trust), Platt calibration, sycophancy detector, shadow eval,
circuit breaker, deadline-aware oracle selection, SOC2/GDPR/HIPAA/ISO
compliance subsystem, operator dashboard.
