"""Real vector KB index using sentence-transformers.

SOTA open-source dense retrieval over the customer's KB. Default encoder
is `sentence-transformers/all-MiniLM-L6-v2` — the standard small fast
encoder used in production RAG stacks.

Implements the same `VectorIndex` Protocol as `LexicalKBIndex`, so it can
be passed directly into `CustomerKBOracle(index=VectorKBIndex())`.

Heavy dependencies (sentence-transformers, numpy) imported lazily.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trustlens.oracles.customer_kb import KBDocument


@dataclass
class VectorKBIndex:
    """Dense vector retrieval over a customer-owned corpus.

    Args:
        model_name: sentence-transformers model id.
        device: "cuda" / "cpu" / None (auto).
        normalize_embeddings: L2-normalize embeddings (recommended for
            cosine similarity).
    """

    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    device: Optional[str] = None
    normalize_embeddings: bool = True

    def __post_init__(self) -> None:
        import numpy as np  # noqa: F401
        from sentence_transformers import SentenceTransformer  # type: ignore

        self._model = SentenceTransformer(self.model_name, device=self.device)
        self._docs_by_tenant: dict[str, list[KBDocument]] = {}
        self._embeddings_by_tenant: dict[str, "np.ndarray"] = {}  # type: ignore[name-defined]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, doc: KBDocument, tenant_id: str = "_default") -> None:
        self.add_many([doc], tenant_id=tenant_id)

    def add_many(self, docs: list[KBDocument], tenant_id: str = "_default") -> None:
        if not docs:
            return
        import numpy as np

        existing_docs = self._docs_by_tenant.setdefault(tenant_id, [])
        new_texts = [d.text for d in docs]
        new_emb = self._model.encode(
            new_texts,
            normalize_embeddings=self.normalize_embeddings,
            convert_to_numpy=True,
        )
        existing_docs.extend(docs)
        existing_emb = self._embeddings_by_tenant.get(tenant_id)
        if existing_emb is None:
            self._embeddings_by_tenant[tenant_id] = new_emb
        else:
            self._embeddings_by_tenant[tenant_id] = np.vstack([existing_emb, new_emb])

    async def search(
        self, query: str, tenant_id: Optional[str], top_k: int
    ) -> list[tuple[KBDocument, float]]:
        import numpy as np

        key = tenant_id or "_default"
        docs = self._docs_by_tenant.get(key) or self._docs_by_tenant.get("_default", [])
        emb = self._embeddings_by_tenant.get(key)
        if emb is None:
            emb = self._embeddings_by_tenant.get("_default")
        if not docs or emb is None:
            return []

        q_emb = self._model.encode(
            [query],
            normalize_embeddings=self.normalize_embeddings,
            convert_to_numpy=True,
        )[0]
        # Cosine similarity (assumes normalized embeddings)
        scores = emb @ q_emb
        order = np.argsort(-scores)[:top_k]
        return [(docs[int(i)], float(scores[int(i)])) for i in order]
