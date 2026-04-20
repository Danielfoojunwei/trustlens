"""Extended admin routes for KB CRUD, integrations, incidents, 3-axis,
and per-feature settings — all permission-gated."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from trustlens.auth.dependencies import require_permission, current_user_or_none
from trustlens.auth.rbac import Permission
from trustlens.auth.users import User
from trustlens.incidents import Incident, IncidentRecorder
from trustlens.integrations import (
    INTEGRATION_KINDS, Integration, IntegrationsStore,
)
from trustlens.kb.versioning import VersionedKB
from trustlens.oracles.customer_kb import KBDocument
from trustlens.verifier.axes import AxisLog


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class DocIn(BaseModel):
    doc_id: str
    text: str
    source_uri: Optional[str] = None
    metadata: dict = {}


class BulkUpsertBody(BaseModel):
    tenant_id: str
    documents: list[DocIn]


class DeleteBody(BaseModel):
    tenant_id: str
    doc_ids: list[str]


class RevertBody(BaseModel):
    tenant_id: str
    version: int


class IntegrationIn(BaseModel):
    # ``kind`` lives in the URL path, so the body only needs the mutable fields.
    kind: Optional[str] = None
    name: Optional[str] = None
    enabled: Optional[bool] = None
    config: Optional[dict] = None


class AckBody(BaseModel):
    note: Optional[str] = None


class FeatureSettings(BaseModel):
    """Persisted per-feature knobs."""
    sycophancy_enabled: Optional[bool] = None
    negation_aware_enabled: Optional[bool] = None
    numeric_aware_enabled: Optional[bool] = None
    transformer_nli_enabled: Optional[bool] = None
    deep_inspector_default: Optional[bool] = None
    ssh_threshold_rho: Optional[float] = None
    ssh_compute_every_n: Optional[int] = None
    steering_alpha: Optional[float] = None
    steering_top_k_layers: Optional[int] = None


class SettingsStore:
    """Thread-safe in-process settings store.

    If ``path`` is supplied, updates are atomically persisted to a JSON file
    and re-loaded on restart. For multi-replica HA, back this with a shared
    store (Postgres / Consul) that implements the same two-method interface.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        import json as _json
        import os as _os
        from pathlib import Path
        from threading import Lock

        self._path = Path(path) if path else None
        self._lock = Lock()
        self._s: dict = FeatureSettings().model_dump()
        if self._path and self._path.exists():
            try:
                loaded = _json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._s.update(loaded)
            except (OSError, ValueError):
                # Corrupt / unreadable file — keep defaults rather than crash
                pass

    def get(self) -> dict:
        with self._lock:
            return dict(self._s)

    def update(self, patch: dict) -> dict:
        with self._lock:
            for k, v in patch.items():
                if v is not None:
                    self._s[k] = v
            if self._path is not None:
                self._persist_locked()
            return dict(self._s)

    def _persist_locked(self) -> None:
        import json as _json
        import os as _os

        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            _json.dumps(self._s, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        _os.replace(tmp, self._path)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def build_ops_router(
    *,
    kb: VersionedKB,
    integrations: IntegrationsStore,
    incidents: IncidentRecorder,
    axes: AxisLog,
    settings: SettingsStore,
) -> APIRouter:
    router = APIRouter(prefix="/v1/admin", tags=["admin-ops"])

    # ----------------------------------------------------------- KB CRUD
    @router.get("/kb/{tenant_id}/docs")
    async def list_docs(
        tenant_id: str,
        _: User = Depends(require_permission(Permission.KB_READ)),
    ) -> list[dict]:
        return [{
            "doc_id": d.doc_id, "text": d.text,
            "source_uri": d.source_uri, "metadata": d.metadata,
        } for d in kb.list_docs(tenant_id)]

    @router.post("/kb/upsert")
    async def upsert_docs(
        body: BulkUpsertBody,
        user: User = Depends(require_permission(Permission.KB_WRITE)),
    ) -> dict:
        docs = [KBDocument(
            doc_id=d.doc_id, text=d.text,
            source_uri=d.source_uri, metadata=d.metadata,
        ) for d in body.documents]
        v = kb.bulk_upsert(body.tenant_id, docs, committed_by=user.email)
        return {"ok": True, "version": v.version,
                "doc_count": v.doc_count, "summary": v.summary}

    @router.post("/kb/delete")
    async def delete_docs(
        body: DeleteBody,
        user: User = Depends(require_permission(Permission.KB_DELETE)),
    ) -> dict:
        v = kb.delete_docs(body.tenant_id, body.doc_ids,
                           committed_by=user.email)
        return {"ok": True, "version": v.version,
                "doc_count": v.doc_count, "summary": v.summary}

    @router.get("/kb/{tenant_id}/versions")
    async def list_versions(
        tenant_id: str,
        _: User = Depends(require_permission(Permission.KB_READ)),
    ) -> list[dict]:
        return [{
            "version": v.version, "committed_at": v.committed_at,
            "doc_count": v.doc_count, "summary": v.summary,
            "committed_by": v.committed_by,
        } for v in kb.versions(tenant_id)]

    @router.post("/kb/revert")
    async def revert(
        body: RevertBody,
        user: User = Depends(require_permission(Permission.KB_WRITE)),
    ) -> dict:
        try:
            v = kb.revert_to(body.tenant_id, body.version,
                             committed_by=user.email)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "new_version": v.version,
                "doc_count": v.doc_count}

    @router.get("/kb/{tenant_id}/export")
    async def export_kb(
        tenant_id: str,
        fmt: str = Query(default="jsonl"),
        _: User = Depends(require_permission(Permission.KB_EXPORT)),
    ) -> PlainTextResponse:
        if fmt == "jsonl":
            body = kb.export_jsonl(tenant_id)
            return PlainTextResponse(
                body, media_type="application/x-ndjson",
                headers={
                    "Content-Disposition":
                        f'attachment; filename="kb-{tenant_id}.jsonl"'
                },
            )
        if fmt == "csv":
            rows = ["doc_id,source_uri,text"]
            for d in kb.list_docs(tenant_id):
                text = d.text.replace('"', '""').replace("\n", " ")
                rows.append(f'{d.doc_id},{d.source_uri or ""},"{text}"')
            return PlainTextResponse(
                "\n".join(rows), media_type="text/csv",
                headers={
                    "Content-Disposition":
                        f'attachment; filename="kb-{tenant_id}.csv"'
                },
            )
        raise HTTPException(status_code=400, detail="unsupported_format")

    # --------------------------------------------------- integrations
    @router.get("/integrations")
    async def list_integrations(
        _: User = Depends(require_permission(Permission.INTEGRATIONS_READ)),
    ) -> list[dict]:
        out = [{
            "kind": i.kind, "name": i.name, "enabled": i.enabled,
            "config_keys": sorted(i.config.keys()),
            "created_at": i.created_at, "updated_at": i.updated_at,
        } for i in integrations.all()]
        out.append({"kind": "__available__", "available": sorted(INTEGRATION_KINDS)})
        return out

    @router.put("/integrations/{kind}")
    async def upsert_integration(
        kind: str, body: IntegrationIn,
        _: User = Depends(require_permission(Permission.INTEGRATIONS_WRITE)),
    ) -> dict:
        if kind not in INTEGRATION_KINDS:
            raise HTTPException(status_code=400, detail="unknown_kind")
        existing = integrations.get(kind) or Integration(kind=kind, name=kind)
        if body.name is not None:    existing.name = body.name
        if body.enabled is not None: existing.enabled = body.enabled
        if body.config is not None:  existing.config = body.config
        integrations.put(existing)
        return {"ok": True, "kind": kind,
                "enabled": existing.enabled, "name": existing.name}

    @router.delete("/integrations/{kind}")
    async def remove_integration(
        kind: str,
        _: User = Depends(require_permission(Permission.INTEGRATIONS_WRITE)),
    ) -> dict:
        return {"ok": integrations.delete(kind)}

    # ---------------------------------------------------------- incidents
    @router.get("/incidents")
    async def list_incidents(
        limit: int = Query(default=200, ge=1, le=1000),
        severity: Optional[str] = None,
        kind: Optional[str] = None,
        tenant_id: Optional[str] = None,
        acked: Optional[bool] = None,
        _: User = Depends(require_permission(Permission.VIEW_INCIDENTS)),
    ) -> list[dict]:
        return [i.to_dict() for i in incidents.recent(
            limit=limit, severity=severity, kind=kind,
            tenant_id=tenant_id, acked=acked,
        )]

    @router.post("/incidents/{incident_id}/ack")
    async def ack_incident(
        incident_id: str,
        body: AckBody,
        user: User = Depends(require_permission(Permission.INCIDENTS_ACK)),
    ) -> dict:
        inc = incidents.acknowledge(incident_id, user.email)
        if inc is None:
            raise HTTPException(status_code=404, detail="not_found")
        return inc.to_dict()

    @router.get("/incidents/stream")
    async def stream_incidents(
        user: Optional[User] = Depends(current_user_or_none),
    ) -> StreamingResponse:
        # Auth-gated stream; 401 surfaces via SSE error handling client-side
        if user is None:
            raise HTTPException(status_code=401, detail="not_authenticated")

        async def _gen():
            try:
                import asyncio
                yield "event: hello\ndata: {}\n\n"
                async for inc in incidents.stream():
                    yield f"data: {json.dumps(inc.to_dict())}\n\n"
            except asyncio.CancelledError:
                return
        return StreamingResponse(
            _gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------- axes
    @router.get("/axes/recent")
    async def axes_recent(
        limit: int = Query(default=500, ge=1, le=5000),
        since_s: float = Query(default=300.0, ge=1.0, le=86_400.0),
        tenant_id: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_AXES)),
    ) -> list[dict]:
        return [p.to_dict() for p in axes.recent(
            limit=limit, tenant_id=tenant_id, since_s=since_s,
        )]

    @router.get("/axes/summary")
    async def axes_summary(
        window_s: float = Query(default=300.0),
        tenant_id: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_AXES)),
    ) -> dict:
        return axes.summary(window_s=window_s, tenant_id=tenant_id)

    # -------------------------------------------------- feature settings
    @router.get("/settings")
    async def get_settings(
        _: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        return settings.get()

    @router.put("/settings")
    async def update_settings(
        body: FeatureSettings,
        _: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        return settings.update(body.model_dump(exclude_unset=True))

    return router
