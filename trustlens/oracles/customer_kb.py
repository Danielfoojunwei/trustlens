"""Customer knowledge-base oracle.

The most valuable oracle for enterprise — grounds claims in a customer's own
corpus (Confluence, SharePoint, product docs, support tickets, etc.).

Uses a pluggable `VectorIndex` protocol so the same oracle can wrap FAISS,
Pinecone, pgvector, Qdrant, or an in-memory lexical index for development.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

from trustlens.oracles.base import Oracle, OracleQuery, OracleResponse


@dataclass
class KBDocument:
    """A single document in a customer KB."""
    doc_id: str
    text: str
    source_uri: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class VectorIndex(Protocol):
    """Protocol for swappable vector/lexical indices."""

    async def search(
        self, query: str, tenant_id: Optional[str], top_k: int
    ) -> list[tuple[KBDocument, float]]: ...


# ---------------------------------------------------------------------------
# Built-in lexical index (deterministic, dependency-free, dev-friendly).
# ---------------------------------------------------------------------------

class LexicalKBIndex:
    """TF-IDF-lite scoring over an in-memory document set.

    Good for unit tests and small customer tiers. Swap for a real vector DB
    in production.
    """

    _TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-']+")

    def __init__(self) -> None:
        self._docs_by_tenant: dict[str, list[KBDocument]] = {}
        self._df: dict[str, dict[str, int]] = {}  # tenant -> token -> doc freq

    def add(self, doc: KBDocument, tenant_id: str = "_default") -> None:
        self._docs_by_tenant.setdefault(tenant_id, []).append(doc)
        df = self._df.setdefault(tenant_id, {})
        for tok in set(self._tokenize(doc.text)):
            df[tok] = df.get(tok, 0) + 1

    def add_many(self, docs: list[KBDocument], tenant_id: str = "_default") -> None:
        for d in docs:
            self.add(d, tenant_id)

    async def search(
        self, query: str, tenant_id: Optional[str], top_k: int
    ) -> list[tuple[KBDocument, float]]:
        key = tenant_id or "_default"
        docs = self._docs_by_tenant.get(key) or self._docs_by_tenant.get("_default", [])
        if not docs:
            return []
        df = self._df.get(key) or self._df.get("_default", {})
        n_docs = max(len(docs), 1)
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scored: list[tuple[KBDocument, float]] = []
        for d in docs:
            doc_tokens = self._tokenize(d.text)
            if not doc_tokens:
                continue
            score = 0.0
            doc_token_set = set(doc_tokens)
            for qt in query_tokens:
                if qt in doc_token_set:
                    tf = doc_tokens.count(qt) / len(doc_tokens)
                    idf = math.log((n_docs + 1) / (df.get(qt, 0) + 1)) + 1.0
                    score += tf * idf
            if score > 0:
                scored.append((d, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def size(self, tenant_id: Optional[str] = None) -> int:
        """Total number of documents across all tenants, or for a specific tenant."""
        if tenant_id:
            return len(self._docs_by_tenant.get(tenant_id, []))
        return sum(len(docs) for docs in self._docs_by_tenant.values())

    @classmethod
    def _tokenize(cls, text: str) -> list[str]:
        return [m.group(0).lower() for m in cls._TOKEN.finditer(text)]


# ---------------------------------------------------------------------------
# The oracle itself
# ---------------------------------------------------------------------------

class CustomerKBOracle:
    """Grounds claims against a customer-owned corpus."""

    def __init__(
        self,
        index: VectorIndex,
        name: str = "customer_kb",
        top_k: int = 5,
        min_score_for_support: float = 0.15,
    ):
        self.name = name
        self._index = index
        self._top_k = top_k
        self._min_score = min_score_for_support

    async def lookup(self, query: OracleQuery) -> OracleResponse:
        started = datetime.now(timezone.utc)
        hits = await self._index.search(
            query.claim_text, query.tenant_id, self._top_k
        )
        if not hits:
            latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
            return OracleResponse(
                oracle_name=self.name,
                evidence="",
                support=0.0,
                queried_at=started.isoformat(),
                latency_ms=latency_ms,
                response_digest=hashlib.sha256(b"").hexdigest(),
            )

        top_doc, top_score = hits[0]
        evidence = " | ".join(
            f"[{i+1}] {doc.text[:200]}" for i, (doc, _) in enumerate(hits[:3])
        )
        support = min(top_score / 5.0, 0.95) if top_score > self._min_score else 0.0

        body_digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
        latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0

        return OracleResponse(
            oracle_name=self.name,
            evidence=evidence,
            support=support,
            contradiction=0.0,
            source_uri=top_doc.source_uri,
            response_digest=body_digest,
            queried_at=started.isoformat(),
            latency_ms=latency_ms,
            raw={
                "hits": [
                    {
                        "doc_id": doc.doc_id,
                        "score": float(score),
                        "source_uri": doc.source_uri,
                    }
                    for doc, score in hits
                ],
            },
        )

    async def close(self) -> None:
        return None
