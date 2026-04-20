"""Oracle interface.

An oracle grounds claims in external evidence. All oracles — Wikidata,
customer KBs, SEC filings, internal wikis — conform to the same interface
so the verifier can treat them uniformly.

Contracts
---------
1. Oracles MUST respect `deadline_ms` (soft; late responses may be ignored).
2. Oracles MUST NOT raise on transient errors; they return an `error`-tagged
   response so the pipeline can degrade gracefully.
3. Oracles MUST be deterministic enough that identical queries return
   semantically equivalent evidence (for caching to work).
4. `OracleResponse.source_uri` MUST point to a permalink where auditors can
   retrieve the same evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


class OracleError(Exception):
    """Raised only for programmer errors (bad config). Transient failures
    are returned via `OracleResponse.error` instead."""


@dataclass(frozen=True)
class OracleQuery:
    """What the verifier asks an oracle."""
    claim_text: str
    context: str = ""
    tenant_id: Optional[str] = None
    deadline_ms: int = 250


@dataclass
class OracleResponse:
    """What an oracle returns."""
    oracle_name: str
    evidence: str                         # human-readable supporting/refuting text
    support: float                         # [0, 1]
    contradiction: float = 0.0             # [0, 1]
    source_uri: Optional[str] = None
    response_digest: str = ""              # sha256 of the raw body
    queried_at: str = ""                   # ISO-8601 UTC
    latency_ms: float = 0.0
    cache_hit: bool = False
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)


class Oracle(Protocol):
    """Async oracle interface."""

    name: str

    async def lookup(self, query: OracleQuery) -> OracleResponse: ...

    async def close(self) -> None: ...
