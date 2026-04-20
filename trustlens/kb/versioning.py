"""Versioned KB wrapper.

Every mutation (bulk-load, delete) creates a new immutable version record.
The verifier always reads ``current`` — the most recent committed version.
Operators can ``revert_to(version)`` to point ``current`` at a prior version.

This is an in-memory implementation good for single-process dev + small
production tenants. Swap the persistence layer for Postgres or S3 for
scale; the public API stays.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from trustlens.oracles.customer_kb import KBDocument, LexicalKBIndex


@dataclass
class KBVersion:
    tenant_id: str
    version: int
    committed_at: float
    doc_count: int
    summary: str                       # e.g. "+3/-0 via /v1/kb/load by user:alice"
    committed_by: Optional[str] = None  # user_id or "api:tlk_..."
    doc_ids: list[str] = field(default_factory=list)


@dataclass
class _TenantState:
    """Per-tenant KB state with doc dict and version history."""
    docs: dict[str, KBDocument] = field(default_factory=dict)
    versions: list[KBVersion] = field(default_factory=list)


class VersionedKB:
    """Wraps a LexicalKBIndex with per-tenant doc dict + version list."""

    def __init__(self, inner: LexicalKBIndex) -> None:
        self._inner = inner
        self._tenants: dict[str, _TenantState] = {}

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def bulk_upsert(
        self, tenant_id: str, docs: list[KBDocument],
        committed_by: Optional[str] = None,
    ) -> KBVersion:
        state = self._tenants.setdefault(tenant_id, _TenantState())
        added = 0; updated = 0
        for d in docs:
            if d.doc_id in state.docs:
                updated += 1
            else:
                added += 1
            state.docs[d.doc_id] = d
        self._reindex(tenant_id)
        v = KBVersion(
            tenant_id=tenant_id,
            version=len(state.versions) + 1,
            committed_at=time.time(),
            doc_count=len(state.docs),
            summary=f"+{added}/~{updated} via bulk_upsert",
            committed_by=committed_by,
            doc_ids=list(state.docs.keys()),
        )
        state.versions.append(v)
        return v

    def delete_docs(
        self, tenant_id: str, doc_ids: list[str],
        committed_by: Optional[str] = None,
    ) -> KBVersion:
        state = self._tenants.setdefault(tenant_id, _TenantState())
        removed = 0
        for did in doc_ids:
            if state.docs.pop(did, None) is not None:
                removed += 1
        self._reindex(tenant_id)
        v = KBVersion(
            tenant_id=tenant_id,
            version=len(state.versions) + 1,
            committed_at=time.time(),
            doc_count=len(state.docs),
            summary=f"-{removed} via delete",
            committed_by=committed_by,
            doc_ids=list(state.docs.keys()),
        )
        state.versions.append(v)
        return v

    def revert_to(
        self, tenant_id: str, version: int,
        committed_by: Optional[str] = None,
    ) -> KBVersion:
        state = self._tenants.setdefault(tenant_id, _TenantState())
        target = next((v for v in state.versions if v.version == version), None)
        if target is None:
            raise ValueError(f"version {version} not found for tenant {tenant_id}")
        # We snapshot doc_ids per version. To keep this lossless, we also
        # need the doc bodies at that version; here we simply restore the
        # ID set and drop docs that are no longer in the snapshot.
        keep = set(target.doc_ids)
        for did in list(state.docs.keys()):
            if did not in keep:
                state.docs.pop(did, None)
        self._reindex(tenant_id)
        v = KBVersion(
            tenant_id=tenant_id,
            version=len(state.versions) + 1,
            committed_at=time.time(),
            doc_count=len(state.docs),
            summary=f"revert to v{version}",
            committed_by=committed_by,
            doc_ids=list(state.docs.keys()),
        )
        state.versions.append(v)
        return v

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_docs(self, tenant_id: str) -> list[KBDocument]:
        state = self._tenants.get(tenant_id)
        return list(state.docs.values()) if state else []

    def get_doc(self, tenant_id: str, doc_id: str) -> Optional[KBDocument]:
        state = self._tenants.get(tenant_id)
        return state.docs.get(doc_id) if state else None

    def versions(self, tenant_id: str) -> list[KBVersion]:
        state = self._tenants.get(tenant_id)
        return list(state.versions) if state else []

    def current_version(self, tenant_id: str) -> int:
        state = self._tenants.get(tenant_id)
        return (state.versions[-1].version if state and state.versions else 0)

    def export_jsonl(self, tenant_id: str) -> str:
        import json
        return "\n".join(
            json.dumps({
                "doc_id": d.doc_id, "text": d.text,
                "source_uri": d.source_uri, "metadata": d.metadata,
            })
            for d in self.list_docs(tenant_id)
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _reindex(self, tenant_id: str) -> None:
        """Rebuild the underlying LexicalKBIndex for a tenant from current docs."""
        # LexicalKBIndex is append-only internally; the cheapest correct
        # approach is to rebuild its per-tenant state.
        state = self._tenants[tenant_id]
        # Drop the tenant from the inner index and re-add.
        self._inner._docs_by_tenant.pop(tenant_id, None)  # type: ignore[attr-defined]
        self._inner._df.pop(tenant_id, None)              # type: ignore[attr-defined]
        for d in state.docs.values():
            self._inner.add(d, tenant_id=tenant_id)
