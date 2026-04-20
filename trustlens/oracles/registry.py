"""Oracle registry — selects, orders, and parallelizes oracle calls.

The registry is where per-tenant routing lives: a legal-tech customer might
want only Westlaw + customer-KB; a research customer only Wikidata + arXiv.
Oracles are tried in a configured order but dispatched concurrently (with
a shared deadline).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from trustlens.oracles.base import Oracle, OracleQuery, OracleResponse
from trustlens.oracles.cache import OracleCache


@dataclass
class OracleSelection:
    """Ordered list of oracle names the verifier should consult.

    `priority_order` is used only for tie-breaking when aggregating. All
    oracles in the selection are fanned out in parallel under a shared
    deadline.
    """
    priority_order: list[str]
    max_parallel: int = 4
    deadline_ms: int = 250


class OracleRegistry:
    """Routes queries to registered oracles, with caching and fan-out."""

    def __init__(
        self,
        oracles: Optional[list[Oracle]] = None,
        cache: Optional[OracleCache] = None,
    ):
        self._oracles: dict[str, Oracle] = {o.name: o for o in (oracles or [])}
        self._cache = cache or OracleCache()

    def register(self, oracle: Oracle) -> None:
        self._oracles[oracle.name] = oracle

    def names(self) -> list[str]:
        return list(self._oracles.keys())

    def get(self, name: str) -> Optional[Oracle]:
        return self._oracles.get(name)

    async def query_many(
        self,
        query: OracleQuery,
        selection: OracleSelection,
    ) -> list[OracleResponse]:
        """Fan-out query to all selected oracles under a shared deadline.

        Responses from oracles that miss the deadline are still collected if
        they arrive, but the caller sees an `error="deadline"` marker.
        """
        names = [n for n in selection.priority_order if n in self._oracles]
        if not names:
            return []

        sem = asyncio.Semaphore(selection.max_parallel)

        async def one(name: str) -> OracleResponse:
            oracle = self._oracles[name]
            cached = self._cache.get(name, query)
            if cached is not None:
                return cached
            async with sem:
                try:
                    response = await asyncio.wait_for(
                        oracle.lookup(query),
                        timeout=selection.deadline_ms / 1000.0,
                    )
                except asyncio.TimeoutError:
                    response = OracleResponse(
                        oracle_name=name,
                        evidence="",
                        support=0.0,
                        error="deadline_exceeded",
                    )
                except Exception as e:
                    response = OracleResponse(
                        oracle_name=name,
                        evidence="",
                        support=0.0,
                        error=f"oracle_error: {type(e).__name__}: {e}",
                    )
            # Cache even errors (short TTL) to avoid thundering herd
            self._cache.put(name, query, response)
            return response

        tasks = [asyncio.create_task(one(n)) for n in names]
        return await asyncio.gather(*tasks)

    async def close(self) -> None:
        for o in self._oracles.values():
            try:
                await o.close()
            except Exception:
                pass
