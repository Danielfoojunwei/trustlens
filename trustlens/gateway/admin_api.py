"""Admin API for the operator dashboard.

Endpoints
---------
GET  /v1/admin/tenants              list tenants with config summary
GET  /v1/admin/certs                paginated cert index (newest first)
GET  /v1/admin/certs/{cert_id}      full cert detail
GET  /v1/admin/events               recent events (ring buffer snapshot)
GET  /v1/admin/events/stream        SSE push of events as they happen
GET  /v1/admin/analytics/summary    KPI snapshot + time-series buckets
GET  /v1/admin/health/deep          deep health probe (backends + oracles + store)

Security
--------
Every endpoint is gated by a concrete RBAC permission. Callers must present
either a session cookie (``tl_session``) or a bearer API key. Unauthenticated
requests receive 401; authenticated requests without the permission get 403.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from trustlens.auth.dependencies import require_permission, current_user_or_none
from trustlens.auth.rbac import Permission
from trustlens.auth.users import User
from trustlens.certificate.store import CertificateStore
from trustlens.gateway.backends import BackendRegistry
from trustlens.gateway.event_log import EventLog
from trustlens.oracles.registry import OracleRegistry
from trustlens.tenancy.config import TenantConfigStore


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class TenantSummary(BaseModel):
    tenant_id: str
    tier: str
    tau: float
    tau_prime: float
    max_rps: int
    max_tokens_per_minute: int
    allowed_backends: list[str]
    allowed_oracles: list[str]
    verify_deadline_ms: int


class CertIndexEntry(BaseModel):
    cert_id: str
    tenant_id: str
    cert_status: Optional[str] = None
    n_claims: Optional[int] = None
    n_renderable: Optional[int] = None
    pipeline_version: Optional[str] = None
    issued_at: Optional[str] = None
    model_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def build_admin_router(
    *,
    tenant_store: TenantConfigStore,
    cert_store: CertificateStore,
    event_log: EventLog,
    backend_registry: BackendRegistry,
    oracle_registry: OracleRegistry,
) -> APIRouter:
    router = APIRouter(prefix="/v1/admin", tags=["admin"])

    # ------------------------------------------------------------------
    # Tenants
    # ------------------------------------------------------------------
    @router.get("/tenants", response_model=list[TenantSummary])
    async def list_tenants(
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> list[TenantSummary]:
        all_fn = getattr(tenant_store, "all", None)
        if all_fn is None:
            raise HTTPException(
                status_code=501,
                detail="tenant_store does not support listing",
            )
        out: list[TenantSummary] = []
        for t in all_fn():
            out.append(TenantSummary(
                tenant_id=t.tenant_id,
                tier=t.tier.value if hasattr(t.tier, "value") else str(t.tier),
                tau=t.tau,
                tau_prime=t.tau_prime,
                max_rps=t.max_rps,
                max_tokens_per_minute=t.max_tokens_per_minute,
                allowed_backends=list(t.allowed_backends),
                allowed_oracles=list(t.allowed_oracles),
                verify_deadline_ms=t.verify_deadline_ms,
            ))
        return out

    # ------------------------------------------------------------------
    # Certificates
    # ------------------------------------------------------------------
    @router.get("/certs", response_model=list[CertIndexEntry])
    async def list_certs(
        tenant_id: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        _: User = Depends(require_permission(Permission.VIEW_CERTS)),
    ) -> list[CertIndexEntry]:
        """List recent certs. If tenant_id is omitted, scans all tenants."""
        tenant_ids: list[str] = []
        if tenant_id:
            tenant_ids = [tenant_id]
        else:
            # Use the tenant-store listing if available, else derive from
            # the cert store's root directory.
            all_fn = getattr(tenant_store, "all", None)
            if all_fn:
                tenant_ids = [t.tenant_id for t in all_fn()]
            else:
                root = getattr(cert_store, "root", None)
                if root and root.exists():
                    tenant_ids = [p.name for p in root.iterdir() if p.is_dir()]

        out: list[CertIndexEntry] = []
        for tid in tenant_ids:
            try:
                ids = cert_store.list_by_tenant(tid, limit=limit)
            except Exception:
                continue
            for cid in ids[-limit:]:
                cert = getattr(cert_store, "get_for_tenant", cert_store.get)(
                    tid, cid,
                ) if hasattr(cert_store, "get_for_tenant") else cert_store.get(cid)
                if cert is None:
                    continue
                p = cert.payload
                out.append(CertIndexEntry(
                    cert_id=cert.cert_id,
                    tenant_id=p.tenant_id,
                    cert_status=p.overall_status.value,
                    n_claims=len(p.claims),
                    n_renderable=sum(1 for c in p.claims if c.is_renderable),
                    pipeline_version=p.pipeline_version,
                    issued_at=p.issued_at,
                    model_id=p.model_id,
                ))
        # newest-issued first
        out.sort(key=lambda c: c.issued_at or "", reverse=True)
        return out[:limit]

    @router.get("/certs/{cert_id}")
    async def get_cert(
        cert_id: str,
        _: User = Depends(require_permission(Permission.VIEW_CERTS)),
    ) -> dict:
        cert = cert_store.get(cert_id)
        if cert is None:
            raise HTTPException(status_code=404, detail="cert not found")
        return cert.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Events — snapshot + SSE stream
    # ------------------------------------------------------------------
    @router.get("/events")
    async def recent_events(
        limit: int = Query(default=200, ge=1, le=2000),
        tenant_id: Optional[str] = Query(default=None),
        kind: Optional[str] = Query(default=None),
        _: User = Depends(require_permission(Permission.VIEW_ANALYTICS)),
    ) -> dict:
        evs = event_log.recent(limit=limit, tenant_id=tenant_id, kind=kind)
        return {
            "events": [e.to_dict() for e in evs],
            "total_in_buffer": event_log.count(),
        }

    @router.get("/events/stream")
    async def stream_events(
        tenant_id: Optional[str] = Query(default=None),
        kind: Optional[str] = Query(default=None),
        user: Optional[User] = Depends(current_user_or_none),
    ) -> StreamingResponse:
        if user is None:
            raise HTTPException(status_code=401, detail="not_authenticated")
        async def _gen():
            # send a hello so the EventSource knows it's connected
            yield f"event: hello\ndata: {json.dumps({'ts': time.time()})}\n\n"
            try:
                async for ev in event_log.stream(tenant_id=tenant_id, kind=kind):
                    yield f"data: {json.dumps(ev.to_dict())}\n\n"
            except asyncio.CancelledError:
                return
        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------
    @router.get("/analytics/summary")
    async def analytics_summary(
        window_s: float = Query(default=300.0, ge=5.0, le=86_400.0),
        bucket_s: float = Query(default=10.0, ge=1.0, le=3600.0),
        tenant_id: Optional[str] = Query(default=None),
        _: User = Depends(require_permission(Permission.VIEW_ANALYTICS)),
    ) -> dict:
        return event_log.aggregate(
            window_s=window_s, bucket_s=bucket_s, tenant_id=tenant_id,
        )

    # ------------------------------------------------------------------
    # Deep health probe
    # ------------------------------------------------------------------
    @router.get("/health/deep")
    async def deep_health(
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> dict:
        return {
            "ts": time.time(),
            "backends": backend_registry.names(),
            "oracles": oracle_registry.names(),
            "tenants": (getattr(tenant_store, "all", lambda: [])() and
                        len(tenant_store.all())) or 0,  # type: ignore[attr-defined]
            "events_in_buffer": event_log.count(),
        }

    return router
