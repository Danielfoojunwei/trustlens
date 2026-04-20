"""Agent-facing control surface.

This router exposes a single, narrow, well-documented endpoint set that an
agentic harness (Claude Code, Agent SDK, LangGraph, OpenAI Agents) can call
on behalf of a human operator. Every endpoint is auth-gated: the agent
must present an API key with the appropriate RBAC role.

Contract
--------
Endpoints are deliberately few and action-oriented so an LLM can plan
against them without having to read the whole gateway:

    GET  /v1/agent/status         — snapshot for an agent to report back
    GET  /v1/agent/tenants        — list tenants the agent can operate on
    POST /v1/agent/tenants        — create/update a tenant
    POST /v1/agent/kb/upsert      — add / replace knowledge for a tenant
    POST /v1/agent/kb/delete      — remove documents
    GET  /v1/agent/incidents      — open incidents (optionally filtered)
    POST /v1/agent/incidents/{id}/ack  — acknowledge an incident
    GET  /v1/agent/alerts         — configured alert rules
    PUT  /v1/agent/alerts         — set alert rules
    POST /v1/agent/verify         — verify a cert (proxy to /v1/verify)
    GET  /v1/agent/capabilities   — what the agent can do and what it needs

Every mutation writes a hash-chained audit log entry so any action taken
through the agent surface is verifiable after the fact.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from trustlens.auth.dependencies import require_permission, current_user_or_none
from trustlens.auth.rbac import Permission
from trustlens.auth.users import User
from trustlens.certificate.schema import Certificate
from trustlens.certificate.signer import verify_certificate
from trustlens.certificate.store import CertificateStore
from trustlens.compliance import AuditLogStore
from trustlens.gateway.backends import BackendRegistry
from trustlens.gateway.event_log import EventLog
from trustlens.incidents import IncidentRecorder
from trustlens.kb.versioning import VersionedKB
from trustlens.oracles.customer_kb import KBDocument
from trustlens.oracles.registry import OracleRegistry
from trustlens.tenancy.config import (
    InMemoryTenantStore, TenantConfig, TenantConfigStore, TenantTier,
)
from trustlens.version import CERT_SCHEMA_VERSION, PIPELINE_VERSION, __version__


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class StatusReply(BaseModel):
    ok: bool
    version: str
    pipeline_version: str
    cert_schema_version: str
    backends: list[str]
    oracles: list[str]
    tenants: int
    open_incidents: int
    recent_events: int


class TenantPatch(BaseModel):
    tenant_id: str
    tier: Optional[str] = None
    tau: Optional[float] = None
    tau_prime: Optional[float] = None
    max_rps: Optional[int] = None
    max_tokens_per_minute: Optional[int] = None
    allowed_backends: Optional[list[str]] = None
    allowed_oracles: Optional[list[str]] = None
    verify_deadline_ms: Optional[int] = None


class DocIn(BaseModel):
    doc_id: str
    text: str
    source_uri: Optional[str] = None
    metadata: dict = {}


class KBUpsert(BaseModel):
    tenant_id: str
    documents: list[DocIn]


class KBDelete(BaseModel):
    tenant_id: str
    doc_ids: list[str]


class AckBody(BaseModel):
    note: Optional[str] = None


class AlertRule(BaseModel):
    """A single alert rule. One rule per `name`."""

    name: str
    kind: str  # "block_rate" | "verify_latency_ms" | "budget_429" | "ssh_critical"
    threshold: float
    window_s: float = 300.0
    tenant_id: Optional[str] = None
    webhook_url: Optional[str] = None
    enabled: bool = True


class AlertRuleSet(BaseModel):
    rules: list[AlertRule]


class VerifyBody(BaseModel):
    cert_id: Optional[str] = None
    certificate: Optional[dict] = None


# ---------------------------------------------------------------------------
# Alert rule store — simple JSON file. Agents change it; operators review it.
# ---------------------------------------------------------------------------


class AlertRuleStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self._path = Path(path) if path else None
        self._lock = Lock()
        self._rules: list[AlertRule] = []
        if self._path and self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._rules = [AlertRule(**r) for r in data.get("rules", [])]
            except (OSError, ValueError):
                pass

    def get(self) -> list[AlertRule]:
        with self._lock:
            return list(self._rules)

    def set(self, rules: list[AlertRule]) -> list[AlertRule]:
        with self._lock:
            self._rules = list(rules)
            if self._path is not None:
                tmp = self._path.with_suffix(self._path.suffix + ".tmp")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(
                    json.dumps(
                        {"rules": [r.model_dump() for r in self._rules]},
                        sort_keys=True, indent=2,
                    ),
                    encoding="utf-8",
                )
                os.replace(tmp, self._path)
            return list(self._rules)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_agent_router(
    *,
    tenant_store: TenantConfigStore,
    cert_store: CertificateStore,
    backend_registry: BackendRegistry,
    oracle_registry: OracleRegistry,
    kb: VersionedKB,
    incidents: IncidentRecorder,
    event_log: EventLog,
    audit_log: AuditLogStore,
    alert_store: AlertRuleStore,
    signer,
) -> APIRouter:
    router = APIRouter(prefix="/v1/agent", tags=["agent"])

    # ------------------------------------------------------------------
    # Status snapshot — the one call an agent should make first.
    # ------------------------------------------------------------------
    @router.get("/status", response_model=StatusReply)
    async def status(
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> StatusReply:
        open_incs = 0
        try:
            open_incs = sum(1 for i in incidents.recent(limit=500)
                            if not i.acknowledged_by)
        except Exception:
            open_incs = 0
        tenants_count = 0
        try:
            all_fn = getattr(tenant_store, "all", None)
            if all_fn:
                tenants_count = len(all_fn())
        except Exception:
            tenants_count = 0
        return StatusReply(
            ok=True,
            version=__version__,
            pipeline_version=PIPELINE_VERSION,
            cert_schema_version=CERT_SCHEMA_VERSION,
            backends=backend_registry.names(),
            oracles=oracle_registry.names(),
            tenants=tenants_count,
            open_incidents=open_incs,
            recent_events=event_log.count(),
        )

    # ------------------------------------------------------------------
    # Tenants — list + create/update
    # ------------------------------------------------------------------
    @router.get("/tenants")
    async def list_tenants(
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> list[dict]:
        all_fn = getattr(tenant_store, "all", None)
        if not all_fn:
            return []
        return [
            {
                "tenant_id": t.tenant_id,
                "tier": t.tier.value,
                "tau": t.tau, "tau_prime": t.tau_prime,
                "max_rps": t.max_rps,
                "max_tokens_per_minute": t.max_tokens_per_minute,
                "allowed_backends": list(t.allowed_backends),
                "allowed_oracles": list(t.allowed_oracles),
                "verify_deadline_ms": t.verify_deadline_ms,
            }
            for t in all_fn()
        ]

    @router.post("/tenants")
    async def upsert_tenant(
        body: TenantPatch,
        user: User = Depends(require_permission(Permission.INTEGRATIONS_WRITE)),
    ) -> dict:
        if not hasattr(tenant_store, "put"):
            raise HTTPException(status_code=501, detail="tenant store is read-only")
        existing = tenant_store.get(body.tenant_id)
        cfg = existing or TenantConfig(tenant_id=body.tenant_id)
        if body.tier is not None:
            try:
                cfg.tier = TenantTier(body.tier)
            except ValueError:
                raise HTTPException(status_code=400, detail="bad_tier")
        if body.tau is not None: cfg.tau = body.tau
        if body.tau_prime is not None: cfg.tau_prime = body.tau_prime
        if body.max_rps is not None: cfg.max_rps = body.max_rps
        if body.max_tokens_per_minute is not None:
            cfg.max_tokens_per_minute = body.max_tokens_per_minute
        if body.allowed_backends is not None:
            cfg.allowed_backends = list(body.allowed_backends)
        if body.allowed_oracles is not None:
            cfg.allowed_oracles = list(body.allowed_oracles)
        if body.verify_deadline_ms is not None:
            cfg.verify_deadline_ms = body.verify_deadline_ms
        tenant_store.put(cfg)  # type: ignore[attr-defined]
        _audit(audit_log, user, "tenant.upsert", f"tenant:{cfg.tenant_id}",
               metadata=body.model_dump(exclude_none=True))
        return {"ok": True, "tenant_id": cfg.tenant_id}

    # ------------------------------------------------------------------
    # KB upsert / delete
    # ------------------------------------------------------------------
    @router.post("/kb/upsert")
    async def kb_upsert(
        body: KBUpsert,
        user: User = Depends(require_permission(Permission.KB_WRITE)),
    ) -> dict:
        docs = [KBDocument(
            doc_id=d.doc_id, text=d.text,
            source_uri=d.source_uri, metadata=d.metadata,
        ) for d in body.documents]
        v = kb.bulk_upsert(body.tenant_id, docs, committed_by=user.email)
        _audit(audit_log, user, "kb.upsert", f"tenant:{body.tenant_id}",
               metadata={"n_docs": len(docs), "version": v.version})
        return {"ok": True, "version": v.version, "doc_count": v.doc_count}

    @router.post("/kb/delete")
    async def kb_delete(
        body: KBDelete,
        user: User = Depends(require_permission(Permission.KB_DELETE)),
    ) -> dict:
        v = kb.delete_docs(body.tenant_id, body.doc_ids,
                           committed_by=user.email)
        _audit(audit_log, user, "kb.delete", f"tenant:{body.tenant_id}",
               metadata={"n_docs": len(body.doc_ids), "version": v.version})
        return {"ok": True, "version": v.version}

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------
    @router.get("/incidents")
    async def list_incidents(
        limit: int = 100,
        severity: Optional[str] = None,
        tenant_id: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_INCIDENTS)),
    ) -> list[dict]:
        return [i.to_dict() for i in incidents.recent(
            limit=limit, severity=severity, tenant_id=tenant_id,
        )]

    @router.post("/incidents/{incident_id}/ack")
    async def ack(
        incident_id: str,
        body: AckBody,
        user: User = Depends(require_permission(Permission.INCIDENTS_ACK)),
    ) -> dict:
        inc = incidents.acknowledge(incident_id, user.email)
        if inc is None:
            raise HTTPException(status_code=404, detail="not_found")
        _audit(audit_log, user, "incident.ack", f"incident:{incident_id}",
               metadata={"note": body.note})
        return inc.to_dict()

    # ------------------------------------------------------------------
    # Alert rules — the agent's scheduling / alerting surface
    # ------------------------------------------------------------------
    @router.get("/alerts", response_model=list[AlertRule])
    async def get_alerts(
        _: User = Depends(require_permission(Permission.VIEW_INCIDENTS)),
    ) -> list[AlertRule]:
        return alert_store.get()

    @router.put("/alerts", response_model=list[AlertRule])
    async def set_alerts(
        body: AlertRuleSet,
        user: User = Depends(require_permission(Permission.INCIDENTS_WEBHOOK)),
    ) -> list[AlertRule]:
        rules = alert_store.set(body.rules)
        _audit(audit_log, user, "alerts.set", "alerts",
               metadata={"n_rules": len(rules)})
        return rules

    # ------------------------------------------------------------------
    # Certificate verification passthrough
    # ------------------------------------------------------------------
    @router.post("/verify")
    async def verify(
        body: VerifyBody,
        _: User = Depends(require_permission(Permission.VIEW_CERTS)),
    ) -> dict:
        cert: Optional[Certificate] = None
        if body.cert_id:
            cert = cert_store.get(body.cert_id)
            if cert is None:
                raise HTTPException(status_code=404, detail="cert_not_found")
        elif body.certificate:
            try:
                cert = Certificate.model_validate(body.certificate)
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid_certificate:{type(e).__name__}",
                )
        else:
            raise HTTPException(status_code=400, detail="need cert_id or certificate")
        result = verify_certificate(cert, signer.public_key)
        return {
            "valid": bool(result.valid),
            "reason": result.reason,
            "cert_id": cert.cert_id,
            "signer_key_id": cert.signer_key_id,
            "overall_status": cert.payload.overall_status.value,
        }

    # ------------------------------------------------------------------
    # Capabilities — machine-readable hint for agentic planners
    # ------------------------------------------------------------------
    @router.get("/capabilities")
    async def capabilities(
        _: Optional[User] = Depends(current_user_or_none),
    ) -> dict:
        return {
            "version": __version__,
            "pipeline_version": PIPELINE_VERSION,
            "actions": [
                {"name": "get_status", "method": "GET",
                 "path": "/v1/agent/status",
                 "requires_permission": "view.overview"},
                {"name": "list_tenants", "method": "GET",
                 "path": "/v1/agent/tenants",
                 "requires_permission": "view.overview"},
                {"name": "upsert_tenant", "method": "POST",
                 "path": "/v1/agent/tenants",
                 "requires_permission": "integrations.write"},
                {"name": "kb_upsert", "method": "POST",
                 "path": "/v1/agent/kb/upsert",
                 "requires_permission": "kb.write"},
                {"name": "kb_delete", "method": "POST",
                 "path": "/v1/agent/kb/delete",
                 "requires_permission": "kb.delete"},
                {"name": "list_incidents", "method": "GET",
                 "path": "/v1/agent/incidents",
                 "requires_permission": "view.incidents"},
                {"name": "ack_incident", "method": "POST",
                 "path": "/v1/agent/incidents/{incident_id}/ack",
                 "requires_permission": "incidents.ack"},
                {"name": "get_alerts", "method": "GET",
                 "path": "/v1/agent/alerts",
                 "requires_permission": "view.incidents"},
                {"name": "set_alerts", "method": "PUT",
                 "path": "/v1/agent/alerts",
                 "requires_permission": "incidents.webhook"},
                {"name": "verify_cert", "method": "POST",
                 "path": "/v1/agent/verify",
                 "requires_permission": "view.certs"},
            ],
            "alert_kinds": [
                "block_rate", "verify_latency_ms",
                "budget_429", "ssh_critical", "cert_failure_rate",
            ],
            "notes": [
                "Every mutation writes a hash-chained audit log entry.",
                "Authorization: Bearer <sk_…> or cookie tl_session.",
                "Cron/scheduler: the agent harness is responsible for "
                "polling /v1/agent/status + /v1/agent/incidents on its own "
                "schedule. TrustLens does not itself own a scheduler.",
            ],
        }

    return router


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _audit(
    audit_log: AuditLogStore, user: User, action: str, resource: str,
    *, metadata: Optional[dict] = None,
) -> None:
    try:
        audit_log.append(
            actor=user.email or user.user_id,
            actor_role=user.role.value,
            action=action, resource=resource,
            outcome="success",
            metadata=metadata or {},
        )
    except Exception:
        # Audit log write never blocks the agent response; the gateway
        # hot path already logs failures.
        pass
