"""KB admin endpoints.

POST /v1/kb/load    — bulk-load documents into the KB index.
GET  /v1/kb/status  — index size, coverage stats, last update timestamp.

These are operator/admin endpoints, not in the hot path. In production they
should be authenticated separately (e.g. an admin API key). The gateway mounts
them under the same app for simplicity; operators can restrict access via
reverse-proxy rules.

Payload for POST /v1/kb/load:
    {
      "tenant_id": "acme",
      "documents": [
        {"doc_id": "doc-1", "text": "...", "source_uri": "kb://doc-1"}
      ]
    }

Supports both LexicalKBIndex (text) and optionally VectorKBIndex (dense).
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from trustlens.oracles.customer_kb import KBDocument, LexicalKBIndex


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class DocumentIn(BaseModel):
    doc_id: str
    text: str
    source_uri: Optional[str] = None


class LoadRequest(BaseModel):
    tenant_id: str = "default"
    documents: list[DocumentIn] = Field(default_factory=list)


class LoadResponse(BaseModel):
    loaded: int
    skipped: int
    tenant_id: str
    index_size: int


class StatusResponse(BaseModel):
    tenant_id: str
    index_size: int
    loaded_at: Optional[float] = None      # Unix timestamp of last load
    unique_tenants: int = 1


# ---------------------------------------------------------------------------
# State tracked by the router (simple in-memory; injected at build time)
# ---------------------------------------------------------------------------

class KBAdminState:
    def __init__(self, kb_index: LexicalKBIndex):
        self._kb = kb_index
        self._loaded_at: Optional[float] = None
        self._total_loaded: int = 0

    def load_documents(self, tenant_id: str, docs: list[DocumentIn]) -> LoadResponse:
        loaded = 0
        skipped = 0
        for d in docs:
            kb_doc = KBDocument(
                doc_id=d.doc_id,
                text=d.text,
                source_uri=d.source_uri or f"kb://{d.doc_id}",
            )
            self._kb.add(kb_doc, tenant_id=tenant_id)
            loaded += 1
        self._loaded_at = time.time()
        self._total_loaded += loaded
        return LoadResponse(
            loaded=loaded,
            skipped=skipped,
            tenant_id=tenant_id,
            index_size=self._kb.size(),
        )

    def status(self, tenant_id: str) -> StatusResponse:
        return StatusResponse(
            tenant_id=tenant_id,
            index_size=self._kb.size(),
            loaded_at=self._loaded_at,
        )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def build_kb_router(kb_index: LexicalKBIndex) -> APIRouter:
    """Return a FastAPI router mounted at /v1/kb."""
    state = KBAdminState(kb_index)
    router = APIRouter(prefix="/v1/kb", tags=["kb-admin"])

    @router.post("/load", response_model=LoadResponse)
    async def load_documents(body: LoadRequest) -> LoadResponse:
        """Bulk-load documents into the KB index for a tenant."""
        return state.load_documents(body.tenant_id, body.documents)

    @router.get("/status", response_model=StatusResponse)
    async def kb_status(tenant_id: str = "default") -> StatusResponse:
        """Return KB index size and last-update stats."""
        return state.status(tenant_id)

    return router
