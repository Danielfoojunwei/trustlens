"""Wikidata oracle.

Queries the public Wikidata SPARQL endpoint for entities referenced in a
claim. Uses entity-linking (wbsearchentities) to anchor, then fetches labels
and descriptions to build an evidence string.

This is a real HTTP implementation. In production:
    - deploy a Wikidata mirror to avoid the public-endpoint rate limits
    - add allowlists for which properties to fetch per claim class
    - extend with property-level SPARQL for relational claims
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

from trustlens.oracles.base import Oracle, OracleQuery, OracleResponse


_WIKIDATA_SEARCH = "https://www.wikidata.org/w/api.php"
_WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"

# Simple named-entity heuristic — proper nouns and multi-word Capitalized spans.
_ENTITY_PATTERN = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\b"
)


class WikidataOracle:
    """Fact oracle backed by Wikidata."""

    name = "wikidata"

    def __init__(
        self,
        user_agent: str = "TrustLens/1.0 (https://trustlens.ai; oracle)",
        client: Optional[httpx.AsyncClient] = None,
        max_entities_per_query: int = 3,
    ):
        self._user_agent = user_agent
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(5.0, connect=2.0),
        )
        self._owns_client = client is None
        self._max_entities = max_entities_per_query

    async def lookup(self, query: OracleQuery) -> OracleResponse:
        started = datetime.now(timezone.utc)

        entities = self._extract_entities(query.claim_text)
        if not entities:
            return self._empty_response(started)

        evidence_parts: list[str] = []
        source_uris: list[str] = []
        for ent in entities[: self._max_entities]:
            qid = await self._search_entity(ent)
            if not qid:
                continue
            label, description = await self._fetch_entity(qid)
            if description:
                evidence_parts.append(f"{label}: {description}")
                source_uris.append(f"https://www.wikidata.org/wiki/{qid}")

        evidence = " | ".join(evidence_parts)
        support = self._score_support(query.claim_text, evidence)
        contradiction = 0.0

        body_digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
        latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0

        return OracleResponse(
            oracle_name=self.name,
            evidence=evidence,
            support=support,
            contradiction=contradiction,
            source_uri=source_uris[0] if source_uris else None,
            response_digest=body_digest,
            queried_at=started.isoformat(),
            latency_ms=latency_ms,
            raw={"entities": entities, "source_uris": source_uris},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _extract_entities(self, text: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for m in _ENTITY_PATTERN.finditer(text):
            e = m.group(1).strip()
            if e.lower() in {"the", "this", "that"} or len(e) < 3:
                continue
            if e in seen:
                continue
            seen.add(e)
            out.append(e)
        return out

    async def _search_entity(self, term: str) -> Optional[str]:
        params = {
            "action": "wbsearchentities",
            "search": term,
            "language": "en",
            "format": "json",
            "type": "item",
            "limit": 1,
        }
        try:
            r = await self._client.get(
                _WIKIDATA_SEARCH + "?" + urlencode(params)
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            return None
        hits = data.get("search", [])
        if not hits:
            return None
        return hits[0].get("id")

    async def _fetch_entity(self, qid: str) -> tuple[str, str]:
        try:
            r = await self._client.get(_WIKIDATA_ENTITY.format(qid=qid))
            r.raise_for_status()
            data = r.json()
        except Exception:
            return "", ""
        entity = data.get("entities", {}).get(qid, {})
        label = entity.get("labels", {}).get("en", {}).get("value", "")
        description = entity.get("descriptions", {}).get("en", {}).get("value", "")
        return label, description

    def _score_support(self, claim: str, evidence: str) -> float:
        """Lexical overlap between claim and evidence, bounded."""
        if not evidence:
            return 0.0
        claim_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{3,}", claim)}
        evid_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{3,}", evidence)}
        if not claim_tokens:
            return 0.0
        overlap = len(claim_tokens & evid_tokens) / len(claim_tokens)
        return min(overlap, 0.95)

    def _empty_response(self, started: datetime) -> OracleResponse:
        latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
        return OracleResponse(
            oracle_name=self.name,
            evidence="",
            support=0.0,
            queried_at=started.isoformat(),
            latency_ms=latency_ms,
            response_digest=hashlib.sha256(b"").hexdigest(),
        )
