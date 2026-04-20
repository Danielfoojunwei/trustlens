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

import json
import time
import uuid
from typing import Callable, Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from trustlens.certificate.schema import CertificateStatus
from trustlens.certificate.signer import KeyPair, sign_certificate
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
from trustlens.verifier.engine import VerificationRequest, VerifierEngine


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
) -> FastAPI:
    app = FastAPI(title="TrustLens Gateway", version="1.0.0")
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
        return {"status": "ok", "pipeline_version": PIPELINE_VERSION}

    @app.get("/readyz")
    async def readyz() -> dict:
        if not breaker.allow():
            raise HTTPException(status_code=503, detail="circuit_open")
        return {
            "status": "ready",
            "backends": backend_registry.names(),
            "oracles": engine._oracles.names(),  # type: ignore[attr-defined]
        }

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(
            metrics.render(), media_type="text/plain; version=0.0.4"
        )

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
            return _error_response(
                502, "bad_gateway", "backend_error",
                f"upstream {backend.name} failed: {type(e).__name__}",
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
                return _error_response(
                    500, "verification_failed", "verify_error",
                    f"verification failed: {type(e).__name__}",
                )

            cert = sign_certificate(vresult.payload, signer)
            try:
                cert_store.put(cert)
            except Exception:
                metrics.certificate_store_errors_total.inc()

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
                audit_log.append(
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
            except Exception:
                pass

            # 3-axis record
            try:
                axes.record(extract_axes(vresult.payload, annotation.certificate_id))
            except Exception:
                pass

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
            err = {"error": {"type": "backend_error", "message": str(e)}}
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
                except Exception:
                    metrics.certificate_store_errors_total.inc()
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
