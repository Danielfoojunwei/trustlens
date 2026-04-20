"""TrustLens gateway — OpenAI-compatible verified proxy.

Endpoints
---------
POST /v1/chat/completions   — proxy to upstream + verify + sign a cert
GET  /healthz, /readyz      — liveness / readiness
GET  /metrics               — Prometheus

Headers
-------
Authorization: Bearer <api_key>          — maps to tenant (via auth hook)
X-TrustLens-Tenant-Id: <tenant>          — explicit override (trusted deploys only)
X-TrustLens-Certificate-Id: <cert_id>    — returned on every verified response
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from trustlens.certificate.schema import Certificate, CertificateStatus
from trustlens.certificate.signer import KeyPair, sign_certificate, verify_certificate
from trustlens.certificate.store import CertificateStore
from trustlens.gateway.backends import BackendRegistry
from trustlens.gateway.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatChoice,
    ChatMessage,
    ChatUsage,
    ErrorDetails,
    ErrorResponse,
    TrustLensResponseAnnotation,
)
from trustlens.auth import (
    ApiKeyStore, InMemoryApiKeyStore, InMemorySessionStore, InMemoryUserStore,
    LocalAuthProvider, SessionStore, UserStore,
)
from trustlens.auth.dependencies import AuthContext, set_auth_context
from trustlens.auth.providers import AuthProvider
from trustlens.gateway.admin_api import build_admin_router
from trustlens.gateway.agent_routes import AlertRuleStore, build_agent_router
from trustlens.gateway.auth_routes import (
    bootstrap_default_users, build_auth_router,
)
from trustlens.gateway.compliance_routes import build_compliance_router
from trustlens.gateway.dashboard import build_dashboard_router
from trustlens.gateway.event_log import EventLog, GatewayEvent
from trustlens.gateway.kb_admin import build_kb_router
from trustlens.gateway.ops_routes import SettingsStore, build_ops_router
from trustlens.compliance import (
    AuditLogStore, BreachStore, ConsentStore, DSARStore,
    InMemoryAuditLog, InMemoryBreachStore, InMemoryConsentStore,
    InMemoryDSARStore, InMemoryModelCardStore, InMemoryProfileStore,
    InMemoryRetentionStore, InMemoryRiskStore, ModelCardStore,
    ProfileStore, RetentionStore, RiskStore,
)
from trustlens.gateway.verification_tier import oracle_selection_for, resolve_tier
from trustlens.incidents import IncidentRecorder, Severity
from trustlens.integrations import (
    InMemoryIntegrationsStore, IntegrationsStore, default_integrations,
)
from trustlens.kb.versioning import VersionedKB
from trustlens.verifier.axes import AxisLog, extract_axes
from trustlens.oracles.customer_kb import LexicalKBIndex
from trustlens.oracles.registry import OracleSelection
from trustlens.robustness.circuit_breaker import CircuitBreaker
from trustlens.tenancy.budget import BudgetExceeded, BudgetTracker
from trustlens.tenancy.config import TenantConfig, TenantConfigStore, TenantTier
from trustlens.utils.redact import redact_secrets
from trustlens.verifier.engine import VerificationRequest, VerifierEngine


logger = logging.getLogger(__name__)


TenantResolver = Callable[[Optional[str], Optional[str]], Optional[TenantConfig]]
"""(authorization_header, explicit_tenant_id) -> TenantConfig or None."""


def default_tenant_resolver(store: TenantConfigStore) -> TenantResolver:
    """Trivial resolver — looks up by X-TrustLens-Tenant-Id.

    Production deployments replace this with a JWT-verified resolver that
    maps API keys to tenant ids.
    """

    def _resolve(
        authorization: Optional[str], explicit_tenant: Optional[str]
    ) -> Optional[TenantConfig]:
        if explicit_tenant:
            return store.get(explicit_tenant)
        return None

    return _resolve


def build_gateway(
    engine: VerifierEngine,
    signer: KeyPair,
    cert_store: CertificateStore,
    backend_registry: BackendRegistry,
    tenant_store: TenantConfigStore,
    *,
    tenant_resolver: Optional[TenantResolver] = None,
    budget_tracker: Optional[BudgetTracker] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    kb_index: Optional[LexicalKBIndex] = None,
    event_log: Optional[EventLog] = None,
    user_store: Optional[UserStore] = None,
    session_store: Optional[SessionStore] = None,
    api_key_store: Optional[ApiKeyStore] = None,
    auth_provider: Optional[AuthProvider] = None,
    versioned_kb: Optional[VersionedKB] = None,
    integrations_store: Optional[IntegrationsStore] = None,
    incident_recorder: Optional[IncidentRecorder] = None,
    axis_log: Optional[AxisLog] = None,
    settings_store: Optional[SettingsStore] = None,
    audit_log_store: Optional[AuditLogStore] = None,
    consent_store: Optional[ConsentStore] = None,
    dsar_store: Optional[DSARStore] = None,
    retention_store: Optional[RetentionStore] = None,
    breach_store: Optional[BreachStore] = None,
    risk_store: Optional[RiskStore] = None,
    model_card_store: Optional[ModelCardStore] = None,
    profile_store: Optional[ProfileStore] = None,
    cors_origins: Optional[list[str]] = None,
    max_request_bytes: int = 2 * 1024 * 1024,
    per_ip_rps: Optional[float] = None,
    alert_store: Optional[AlertRuleStore] = None,
) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        logger.info("gateway starting")
        try:
            yield
        finally:
            logger.info("gateway shutting down; draining backends")
            try:
                await backend_registry.close_all()
            except Exception as e:
                logger.warning("backend close failed: %s", type(e).__name__)

    app = FastAPI(title="TrustLens Gateway", version="1.0.0", lifespan=_lifespan)

    origins = cors_origins if cors_origins is not None else []
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            allow_headers=["*"],
        )

    # Body size limit — protects against unbounded `messages` arrays on chat.
    _max_bytes = max_request_bytes

    class _SizeLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            cl = request.headers.get("content-length")
            if cl is not None:
                try:
                    if int(cl) > _max_bytes:
                        return Response(
                            content=json.dumps({
                                "error": {
                                    "type": "bad_request",
                                    "code": "payload_too_large",
                                    "message": f"request body exceeds {_max_bytes} bytes",
                                }
                            }),
                            media_type="application/json",
                            status_code=413,
                        )
                except ValueError:
                    pass
            return await call_next(request)

    app.add_middleware(_SizeLimitMiddleware)

    if per_ip_rps is not None and per_ip_rps > 0:
        from trustlens.gateway.ratelimit import PerIPRateLimit
        app.add_middleware(PerIPRateLimit, rps=per_ip_rps)
    resolver = tenant_resolver or default_tenant_resolver(tenant_store)
    budget = budget_tracker or BudgetTracker()
    breaker = circuit_breaker or CircuitBreaker(failure_threshold=50, recovery_time_s=30)
    evlog = event_log or EventLog()

    # Auth + RBAC
    users = user_store or InMemoryUserStore()
    sessions = session_store or InMemorySessionStore()
    api_keys = api_key_store or InMemoryApiKeyStore()
    bootstrap_default_users(users)
    set_auth_context(AuthContext(users=users, sessions=sessions, api_keys=api_keys))
    provider = auth_provider or LocalAuthProvider(users=users)
    app.include_router(build_auth_router(
        users=users, sessions=sessions, api_keys=api_keys, provider=provider,
    ))

    # Versioned KB (wraps the existing lexical index so CRUD works)
    kb_idx = kb_index or LexicalKBIndex()
    vkb = versioned_kb or VersionedKB(kb_idx)

    # Integrations + incidents + axes + settings
    integrations = integrations_store or InMemoryIntegrationsStore(default_integrations())
    incidents = incident_recorder or IncidentRecorder(integrations=integrations)
    axes = axis_log or AxisLog()
    settings = settings_store or SettingsStore()

    # Legacy KB admin (kept for backward compat with existing tests)
    if kb_idx is not None:
        app.include_router(build_kb_router(kb_idx))

    # New admin API used by the dashboard (tenants, certs, events, analytics)
    app.include_router(build_admin_router(
        tenant_store=tenant_store,
        cert_store=cert_store,
        event_log=evlog,
        backend_registry=backend_registry,
        oracle_registry=engine._oracles,  # type: ignore[attr-defined]
    ))

    # Extended ops API (KB CRUD + versions, integrations, incidents, axes, settings)
    app.include_router(build_ops_router(
        kb=vkb, integrations=integrations, incidents=incidents,
        axes=axes, settings=settings,
    ))

    # Compliance subsystem (audit log + DSAR + consent + retention +
    # breach + risk + model cards + profiles + transparency)
    audit_log = audit_log_store or InMemoryAuditLog()
    consent   = consent_store   or InMemoryConsentStore()
    dsar      = dsar_store      or InMemoryDSARStore()
    retention = retention_store or InMemoryRetentionStore()
    breach    = breach_store    or InMemoryBreachStore()
    risk      = risk_store      or InMemoryRiskStore()
    cards     = model_card_store or InMemoryModelCardStore()
    profiles  = profile_store   or InMemoryProfileStore()

    app.include_router(build_compliance_router(
        audit_log=audit_log, consent=consent, dsar=dsar,
        retention=retention, breach=breach, risk=risk,
        model_cards=cards, profiles=profiles,
    ))

    # Store the dashboard-facing helpers on the app for closures below
    app.state.event_log = evlog
    app.state.incidents = incidents
    app.state.axes = axes
    app.state.versioned_kb = vkb
    app.state.integrations = integrations
    app.state.audit_log = audit_log
    app.state.consent = consent
    app.state.dsar = dsar
    app.state.retention = retention
    app.state.breach = breach
    app.state.risk = risk
    app.state.model_cards = cards
    app.state.profiles = profiles

    # Agent control surface (/v1/agent/*). Read the SKILL.md at the repo root
    # for how an agentic harness should call these endpoints.
    alerts = alert_store or AlertRuleStore(
        path=os.environ.get("TRUSTLENS_ALERT_STORE"),
    )
    app.include_router(build_agent_router(
        tenant_store=tenant_store,
        cert_store=cert_store,
        backend_registry=backend_registry,
        oracle_registry=engine._oracles,  # type: ignore[attr-defined]
        kb=vkb,
        incidents=incidents,
        event_log=evlog,
        audit_log=audit_log,
        alert_store=alerts,
        signer=signer,
    ))
    app.state.alert_store = alerts

    # Mount the operator dashboard at /dashboard (redirects / → /dashboard)
    app.include_router(build_dashboard_router())

    try:
        from trustlens.observability.metrics import Metrics
        metrics = Metrics()
    except Exception:
        from trustlens.observability.metrics import NullMetrics
        metrics = NullMetrics()  # type: ignore[assignment]

    @app.get("/healthz")
    async def healthz() -> dict:
        from trustlens.version import PIPELINE_VERSION
        checks: dict[str, str] = {}
        try:
            _ = signer.key_id
            checks["signer"] = "ok"
        except Exception:
            checks["signer"] = "fail"
        try:
            _ = backend_registry.names()
            checks["backend_registry"] = "ok"
        except Exception:
            checks["backend_registry"] = "fail"
        try:
            _ = cert_store is not None
            checks["cert_store"] = "ok" if cert_store is not None else "fail"
        except Exception:
            checks["cert_store"] = "fail"
        ok = all(v == "ok" for v in checks.values())
        body = {
            "status": "ok" if ok else "degraded",
            "pipeline_version": PIPELINE_VERSION,
            "checks": checks,
        }
        if not ok:
            raise HTTPException(status_code=503, detail=body)
        return body

    @app.get("/readyz")
    async def readyz() -> dict:
        if not breaker.allow():
            raise HTTPException(status_code=503, detail="circuit_open")
        backend_names = backend_registry.names()
        oracle_names = engine._oracles.names()  # type: ignore[attr-defined]
        if not backend_names:
            raise HTTPException(status_code=503, detail="no_backends")
        return {
            "status": "ready",
            "backends": backend_names,
            "oracles": oracle_names,
        }

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(
            metrics.render(), media_type="text/plain; version=0.0.4"
        )

    # Server-side certificate verification. Accepts either a stored cert_id
    # (looked up and re-verified) or an inline certificate JSON object. The
    # signature is checked against the gateway's own signer public key.
    @app.post("/v1/verify")
    async def verify_endpoint(body: dict) -> dict:
        cert_obj: Optional[Certificate] = None
        if "cert_id" in body and body["cert_id"]:
            cert_obj = cert_store.get(body["cert_id"])
            if cert_obj is None:
                raise HTTPException(status_code=404, detail="cert_not_found")
        elif "certificate" in body and body["certificate"]:
            try:
                cert_obj = Certificate.model_validate(body["certificate"])
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid_certificate:{type(e).__name__}",
                )
        else:
            raise HTTPException(
                status_code=400,
                detail="either cert_id or certificate must be provided",
            )

        result = verify_certificate(cert_obj, signer.public_key)
        return {
            "valid": bool(result.valid),
            "reason": result.reason,
            "schema_version_match": result.schema_version_match,
            "pipeline_version_match": result.pipeline_version_match,
            "cert_id": cert_obj.cert_id,
            "signer_key_id": cert_obj.signer_key_id,
            "overall_status": cert_obj.payload.overall_status.value,
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(
        body: ChatCompletionRequest,
        request: Request,
        authorization: Optional[str] = Header(default=None),
        x_trustlens_tenant_id: Optional[str] = Header(default=None),
    ):
        t0 = time.perf_counter()

        # 1. Resolve tenant
        explicit = (body.trustlens.tenant_id if body.trustlens else None) or x_trustlens_tenant_id
        tenant = resolver(authorization, explicit)
        if tenant is None:
            evlog.record(GatewayEvent(
                ts=time.time(), kind="error", tenant_id=explicit,
                method="POST", path="/v1/chat/completions", status_code=401,
                error_code="no_tenant_resolved",
            ))
            return _error_response(
                401, "unauthorized", "no_tenant_resolved", "no valid tenant in request"
            )

        # 2. Circuit breaker
        if not breaker.allow():
            return _error_response(503, "unavailable", "circuit_open", "gateway overloaded")

        # 3. Budget check
        try:
            tokens_estimate = _estimate_tokens(body)
            budget.request(tenant.tenant_id, tenant, tokens_estimate)
        except BudgetExceeded as e:
            metrics.gateway_budget_rejects_total.labels(
                tenant=tenant.tenant_id, kind=e.kind
            ).inc()
            return _error_response(
                429, "rate_limited", e.kind,
                f"budget exceeded: {e.kind}",
                retry_after_s=e.retry_after_s,
            )

        # 4. Select backend
        backend = backend_registry.select(body.model, tenant.allowed_backends)
        if backend is None:
            return _error_response(
                400, "bad_request", "no_backend",
                f"no backend available for model={body.model}",
            )

        # 5. Deep Inspector gating
        wants_deep = body.trustlens and body.trustlens.deep_inspector
        if wants_deep and tenant.tier != TenantTier.DEEP_INSPECTOR:
            return _error_response(
                402, "upgrade_required", "deep_inspector_unavailable",
                "Deep Inspector features require the deep_inspector tier",
            )

        # 6. Determine verify flags + tier
        verify = True
        tau = tenant.tau
        tau_prime = tenant.tau_prime
        oracles_override: Optional[list[str]] = None
        deadline_ms = tenant.verify_deadline_ms
        requested_tier: Optional[str] = None
        if body.trustlens is not None:
            verify = body.trustlens.verify
            tau = body.trustlens.tau if body.trustlens.tau is not None else tau
            tau_prime = body.trustlens.tau_prime if body.trustlens.tau_prime is not None else tau_prime
            oracles_override = body.trustlens.oracles
            if body.trustlens.deadline_ms:
                deadline_ms = body.trustlens.deadline_ms
            requested_tier = body.trustlens.verification_tier

        # Resolve verification tier — configures NLI and oracle set for this request
        all_oracle_names = engine._oracles.names()  # type: ignore[attr-defined]
        tier_config = resolve_tier(requested_tier, all_oracle_names, deadline_ms)

        # 7. Proxy — streaming vs buffered
        if body.stream:
            return await _stream_completion(
                body=body, backend=backend, engine=engine, tenant=tenant,
                signer=signer, cert_store=cert_store, verify=verify,
                tau=tau, tau_prime=tau_prime, oracles_override=oracles_override,
                deadline_ms=tier_config.deadline_ms, metrics=metrics,
                breaker=breaker, t0=t0, budget=budget,
                tier_config=tier_config,
            )

        try:
            upstream = await backend.complete(body)
        except Exception as e:
            breaker.record_failure()
            metrics.gateway_requests_total.labels(
                tenant=tenant.tenant_id, backend=backend.name, status="backend_error",
            ).inc()
            logger.warning(
                "backend_error tenant=%s backend=%s err=%s",
                tenant.tenant_id, backend.name,
                redact_secrets(type(e).__name__),
            )
            return _error_response(
                502, "bad_gateway", "backend_error",
                redact_secrets(f"upstream {backend.name} failed: {type(e).__name__}"),
            )

        # Track actual tokens
        budget.record_tokens_used(
            tenant.tenant_id, upstream.prompt_tokens + upstream.completion_tokens
        )

        renderable = upstream.content
        annotation: Optional[TrustLensResponseAnnotation] = None

        if verify:
            prompt = _prompt_from_messages(body.messages)
            oracles_list = oracles_override or tier_config.oracle_names or tenant.effective_oracles(
                engine._oracles.names()  # type: ignore[attr-defined]
            )
            selection = OracleSelection(
                priority_order=oracles_list,
                deadline_ms=tier_config.deadline_ms,
            )
            # Inject tier-appropriate NLI into the engine for this request
            engine._nli = tier_config.nli  # type: ignore[attr-defined]
            vreq = VerificationRequest(
                prompt=prompt,
                response_text=upstream.content,
                tenant_id=tenant.tenant_id,
                request_id=str(uuid.uuid4()),
                model_id=upstream.model,
                tau=tau,
                tau_prime=tau_prime,
                oracle_selection=selection,
            )
            try:
                vresult = await engine.verify(vreq)
            except Exception as e:
                breaker.record_failure()
                metrics.verifier_errors_total.labels(kind=type(e).__name__).inc()
                logger.exception(
                    "verifier_failed tenant=%s backend=%s",
                    tenant.tenant_id, backend.name,
                )
                return _error_response(
                    500, "verification_failed", "verify_error",
                    redact_secrets(f"verification failed: {type(e).__name__}"),
                )

            cert = sign_certificate(vresult.payload, signer)
            try:
                cert_store.put(cert)
            except Exception as e:
                metrics.certificate_store_errors_total.inc()
                logger.error(
                    "cert_store.put failed cert_id=%s err=%s",
                    cert.cert_id, type(e).__name__,
                )

            # If blocked, suppress the upstream output
            if vresult.payload.overall_status == CertificateStatus.BLOCKED:
                renderable = ""
            else:
                renderable = vresult.renderable_text or upstream.content

            annotation = TrustLensResponseAnnotation(
                certificate_id=cert.cert_id,
                certificate_status=vresult.payload.overall_status.value,
                pipeline_version=vresult.payload.pipeline_version,
                renderable_text_hash=vresult.payload.renderable_text_hash,
                masked_claim_ids=vresult.masked_claim_ids,
                degradations=vresult.payload.degradations,
                certificate=None,  # inline omitted by default
            )

        breaker.record_success()
        latency_ms = (time.perf_counter() - t0) * 1000.0
        metrics.gateway_latency_ms.labels(tenant=tenant.tenant_id).observe(latency_ms)
        metrics.gateway_requests_total.labels(
            tenant=tenant.tenant_id, backend=backend.name, status="ok"
        ).inc()

        # Record for the dashboard
        evlog.record(GatewayEvent(
            ts=time.time(), kind="request", tenant_id=tenant.tenant_id,
            method="POST", path="/v1/chat/completions", status_code=200,
            model=upstream.model, latency_ms=round(latency_ms, 3),
        ))
        if annotation is not None:
            n_claims = len(vresult.payload.claims) if verify else 0
            n_renderable = sum(1 for c in vresult.payload.claims
                               if c.is_renderable) if verify else 0
            evlog.record(GatewayEvent(
                ts=time.time(), kind="cert", tenant_id=tenant.tenant_id,
                cert_id=annotation.certificate_id,
                cert_status=annotation.certificate_status,
                n_claims=n_claims, n_renderable=n_renderable,
                model=upstream.model, latency_ms=round(latency_ms, 3),
            ))
            # Tamper-evident audit log entry — proves a cert was issued.
            try:
                await asyncio.to_thread(
                    audit_log.append,
                    actor=tenant.tenant_id, actor_role="tenant",
                    action="cert.issue", outcome="success",
                    tenant_id=tenant.tenant_id,
                    request_id=body.trustlens.request_id if body.trustlens else None,
                    resource=f"cert:{annotation.certificate_id}",
                    metadata={
                        "status": annotation.certificate_status,
                        "n_claims": n_claims,
                        "n_renderable": n_renderable,
                        "model": upstream.model,
                        "latency_ms": round(latency_ms, 3),
                    },
                )
            except Exception as e:
                logger.error(
                    "audit_log.append failed cert_id=%s err=%s",
                    annotation.certificate_id, type(e).__name__,
                )

            # 3-axis record
            try:
                axes.record(extract_axes(vresult.payload, annotation.certificate_id))
            except Exception as e:
                logger.warning(
                    "axes.record failed cert_id=%s err=%s",
                    annotation.certificate_id, type(e).__name__,
                )

            # Auto-incident heuristics: blocked certs or degraded state
            if annotation.certificate_status == "blocked":
                incidents.record(
                    kind="block_rate.spike", severity=Severity.WARN,
                    title=f"blocked cert for tenant={tenant.tenant_id}",
                    tenant_id=tenant.tenant_id, cert_id=annotation.certificate_id,
                    detail={"masked_claim_ids": annotation.masked_claim_ids,
                            "degradations": annotation.degradations},
                )
            elif annotation.certificate_status == "degraded":
                incidents.record(
                    kind="oracle.slow", severity=Severity.INFO,
                    title=f"degraded verification path",
                    tenant_id=tenant.tenant_id, cert_id=annotation.certificate_id,
                    detail={"degradations": annotation.degradations},
                )
            # SSH / steering events from deep inspector payload
            di = getattr(vresult.payload, "deep_inspector", None)
            if di and isinstance(di, dict):
                for s in (di.get("ssh_snapshots") or []):
                    if s.get("severity") == "critical":
                        incidents.record(
                            kind="ssh.critical", severity=Severity.CRITICAL,
                            title=f"SSH ρ={s.get('rho'):.3f} at step {s.get('step')}",
                            tenant_id=tenant.tenant_id,
                            cert_id=annotation.certificate_id,
                            detail={"step": s.get("step"), "rho": s.get("rho")},
                        )
                        break
                for e in (di.get("steering_events") or []):
                    if e.get("kind") == "engage":
                        incidents.record(
                            kind="radcot.engage", severity=Severity.WARN,
                            title=f"RAD-CoT engaged scale={e.get('scale')} at step {e.get('at_step')}",
                            tenant_id=tenant.tenant_id,
                            cert_id=annotation.certificate_id,
                            detail=e,
                        )

        response = ChatCompletionResponse(
            model=upstream.model,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=renderable),
                    finish_reason=upstream.finish_reason,
                )
            ],
            usage=ChatUsage(
                prompt_tokens=upstream.prompt_tokens,
                completion_tokens=upstream.completion_tokens,
                total_tokens=upstream.prompt_tokens + upstream.completion_tokens,
            ),
            trustlens=annotation,
        )
        # Also expose cert id as a header for non-JSON consumers
        headers = {}
        if annotation is not None:
            headers["X-TrustLens-Certificate-Id"] = annotation.certificate_id
            headers["X-TrustLens-Status"] = annotation.certificate_status
        return Response(
            content=response.model_dump_json(),
            media_type="application/json",
            headers=headers,
        )

    return app


# ---------------------------------------------------------------------------
# Streaming path
# ---------------------------------------------------------------------------

async def _stream_completion(
    body: ChatCompletionRequest,
    backend,
    engine: VerifierEngine,
    tenant: TenantConfig,
    signer: KeyPair,
    cert_store: CertificateStore,
    verify: bool,
    tau: float,
    tau_prime: float,
    oracles_override: Optional[list[str]],
    deadline_ms: int,
    metrics,
    breaker: CircuitBreaker,
    t0: float,
    budget: BudgetTracker,
    tier_config=None,
) -> StreamingResponse:
    """SSE stream: forward upstream deltas, then emit a trailer event with the
    trust certificate once verification completes.

    Important: streaming verification is post-hoc (verify runs after the whole
    response is buffered). True streaming verification requires Deep Inspector
    mode with pre-emission claim checkpoints; that's on the roadmap.
    """

    async def gen():
        accumulated: list[str] = []
        model_out = body.model
        try:
            async for chunk in backend.stream(body):
                if chunk.delta:
                    accumulated.append(chunk.delta)
                    payload = {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                        "object": "chat.completion.chunk",
                        "model": model_out,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": chunk.delta},
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                if chunk.finish_reason:
                    break
        except Exception as e:
            breaker.record_failure()
            err = {"error": {"type": "backend_error",
                             "message": redact_secrets(str(e))}}
            yield f"data: {json.dumps(err)}\n\n"
            yield "data: [DONE]\n\n"
            return

        full_text = "".join(accumulated)

        annotation_dict: Optional[dict] = None
        if verify:
            prompt = _prompt_from_messages(body.messages)
            if tier_config is not None:
                oracles_list = oracles_override or tier_config.oracle_names or tenant.effective_oracles(
                    engine._oracles.names()  # type: ignore[attr-defined]
                )
                engine._nli = tier_config.nli  # type: ignore[attr-defined]
                eff_deadline = tier_config.deadline_ms
            else:
                oracles_list = oracles_override or tenant.effective_oracles(
                    engine._oracles.names()  # type: ignore[attr-defined]
                )
                eff_deadline = min(deadline_ms, 250)
            selection = OracleSelection(
                priority_order=oracles_list,
                deadline_ms=eff_deadline,
            )
            vreq = VerificationRequest(
                prompt=prompt, response_text=full_text,
                tenant_id=tenant.tenant_id,
                request_id=str(uuid.uuid4()),
                model_id=model_out,
                tau=tau, tau_prime=tau_prime,
                oracle_selection=selection,
            )
            try:
                vresult = await engine.verify(vreq)
                cert = sign_certificate(vresult.payload, signer)
                try:
                    cert_store.put(cert)
                except Exception as e:
                    metrics.certificate_store_errors_total.inc()
                    logger.error(
                        "cert_store.put failed cert_id=%s err=%s",
                        cert.cert_id, type(e).__name__,
                    )
                annotation_dict = {
                    "certificate_id": cert.cert_id,
                    "certificate_status": vresult.payload.overall_status.value,
                    "pipeline_version": vresult.payload.pipeline_version,
                    "renderable_text_hash": vresult.payload.renderable_text_hash,
                    "masked_claim_ids": vresult.masked_claim_ids,
                    "degradations": vresult.payload.degradations,
                }
            except Exception as e:
                metrics.verifier_errors_total.labels(kind=type(e).__name__).inc()
                annotation_dict = {
                    "certificate_id": "",
                    "certificate_status": "degraded",
                    "pipeline_version": "",
                    "renderable_text_hash": "",
                    "masked_claim_ids": [],
                    "degradations": [f"verifier_error:{type(e).__name__}"],
                }

        breaker.record_success()
        latency_ms = (time.perf_counter() - t0) * 1000.0
        metrics.gateway_latency_ms.labels(tenant=tenant.tenant_id).observe(latency_ms)
        metrics.gateway_requests_total.labels(
            tenant=tenant.tenant_id, backend=backend.name, status="ok"
        ).inc()

        # Final chunk with finish_reason
        final = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion.chunk",
            "model": model_out,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "trustlens": annotation_dict,
        }
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_response(
    status: int, kind: str, code: str, message: str,
    retry_after_s: Optional[float] = None,
) -> Response:
    err = ErrorResponse(
        error=ErrorDetails(
            type=kind, code=code, message=message, retry_after_s=retry_after_s
        )
    )
    headers = {}
    if retry_after_s is not None:
        headers["Retry-After"] = str(int(retry_after_s) + 1)
    return Response(
        content=err.model_dump_json(),
        media_type="application/json",
        status_code=status,
        headers=headers,
    )


def _prompt_from_messages(msgs: list[ChatMessage]) -> str:
    """Reduce a chat history into a single prompt string for verification."""
    parts = []
    for m in msgs:
        parts.append(f"[{m.role}] {m.content}")
    return "\n".join(parts)


def _estimate_tokens(body: ChatCompletionRequest) -> int:
    """Rough token estimate for admission control."""
    prompt_chars = sum(len(m.content) for m in body.messages)
    prompt_tokens = max(1, prompt_chars // 4)
    completion_tokens = body.max_tokens or 256
    return prompt_tokens + completion_tokens


def build_gateway_from_env() -> FastAPI:
    """Factory for uvicorn's ``--factory`` mode (multi-worker deployments).

    Reads the same env vars as ``trustlens serve-gateway``. Each uvicorn
    worker calls this in its own process.
    """
    import os
    from pathlib import Path

    from trustlens.certificate.signer import KeyPair
    from trustlens.certificate.store import FilesystemStore
    from trustlens.gateway.backends import (
        BackendRegistry, EchoBackend, OpenAICompatBackend,
    )
    from trustlens.oracles.customer_kb import CustomerKBOracle, LexicalKBIndex
    from trustlens.oracles.registry import OracleRegistry
    from trustlens.tenancy.config import (
        InMemoryTenantStore, TenantConfig, TenantTier,
    )
    from trustlens.verifier.engine import VerifierEngine

    key_path = Path(os.environ.get("TRUSTLENS_SIGNER_KEY",
                                   "./.trustlens/signer.pem"))
    store_path = os.environ.get("TRUSTLENS_CERT_STORE", "./.trustlens/certs")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        keypair = KeyPair.from_private_pem(key_path.read_bytes())
    else:
        keypair = KeyPair.generate()
        key_path.write_bytes(keypair.private_pem())

    backends: list = [EchoBackend()]
    allowed = ["echo"]
    backend_url = os.environ.get("TRUSTLENS_BACKEND_URL")
    if backend_url:
        backends.append(OpenAICompatBackend(
            name="openai", base_url=backend_url,
            api_key=os.environ.get("OPENAI_API_KEY"),
        ))
        allowed.append("openai")

    tenants = InMemoryTenantStore([
        TenantConfig(
            tenant_id=os.environ.get("TRUSTLENS_DEMO_TENANT", "demo"),
            tier=TenantTier.PRO,
            allowed_backends=allowed,
        )
    ])
    kb = LexicalKBIndex()
    registry = OracleRegistry([CustomerKBOracle(kb)])
    engine = VerifierEngine(registry)

    cors_origins = [
        o.strip() for o in os.environ.get("TRUSTLENS_CORS_ORIGINS", "").split(",")
        if o.strip()
    ] or None

    return build_gateway(
        engine=engine,
        signer=keypair,
        cert_store=FilesystemStore(store_path),
        backend_registry=BackendRegistry(backends),
        tenant_store=tenants,
        kb_index=kb,
        cors_origins=cors_origins,
    )
