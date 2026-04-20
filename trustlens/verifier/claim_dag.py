"""Claim DAG — compositional claim graph.

A response decomposes into atomic claims. Claims may depend on each other
(e.g. "this river" depends on an earlier claim that named the river). A claim
is *renderable* only if it AND all its transitive predecessors are verified.

This fixes the compositionality gap: token-level verification can pass while
a composed claim is unverifiable. The DAG makes the dependency structure
explicit.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional


class CycleError(ValueError):
    """Raised when the claim dependency graph has a cycle."""


def stable_claim_id(text: str, depends_on: Iterable[str]) -> str:
    """Deterministic content-hash over (text, sorted deps). 16 hex chars."""
    deps_sorted = sorted(depends_on)
    payload = text.strip() + "\x1f" + "\x1e".join(deps_sorted)
    return "c_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class Claim:
    """One atomic claim in the DAG."""
    claim_id: str
    text: str
    depends_on: list[str] = field(default_factory=list)
    span: Optional[tuple[int, int]] = None   # char offsets in source text
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        text: str,
        depends_on: Optional[list[str]] = None,
        span: Optional[tuple[int, int]] = None,
        metadata: Optional[dict] = None,
    ) -> "Claim":
        deps = list(depends_on or [])
        cid = stable_claim_id(text, deps)
        return cls(
            claim_id=cid,
            text=text.strip(),
            depends_on=deps,
            span=span,
            metadata=metadata or {},
        )


class ClaimDAG:
    """A directed acyclic graph of claim dependencies.

    - `add(claim)` registers a node.
    - `topological_order()` returns claims in a safe verification order.
    - `renderable_closure(verified_ids)` returns the set of claim ids that
      can be rendered given a set of verified ids. A claim is renderable iff
      it is in `verified_ids` AND all its predecessors are too.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, Claim] = {}
        self._forward: dict[str, set[str]] = defaultdict(set)  # pred -> {succs}
        self._reverse: dict[str, set[str]] = defaultdict(set)  # succ -> {preds}

    # ------------------------------------------------------------------
    # Graph mutation
    # ------------------------------------------------------------------

    def add(self, claim: Claim) -> None:
        if claim.claim_id in self._nodes:
            # Idempotent: same id must mean same content
            existing = self._nodes[claim.claim_id]
            if existing.text != claim.text or existing.depends_on != claim.depends_on:
                raise ValueError(
                    f"claim_id collision with differing content: {claim.claim_id}"
                )
            return
        self._nodes[claim.claim_id] = claim
        for dep in claim.depends_on:
            self._forward[dep].add(claim.claim_id)
            self._reverse[claim.claim_id].add(dep)

    def get(self, claim_id: str) -> Optional[Claim]:
        return self._nodes.get(claim_id)

    def claims(self) -> list[Claim]:
        return list(self._nodes.values())

    def edges(self) -> list[tuple[str, str]]:
        return [
            (pred, succ)
            for pred, succs in self._forward.items()
            for succ in succs
        ]

    def predecessors(self, claim_id: str) -> set[str]:
        return set(self._reverse.get(claim_id, ()))

    def ancestors(self, claim_id: str) -> set[str]:
        """All transitive predecessors (not including claim_id itself)."""
        out: set[str] = set()
        stack = [claim_id]
        while stack:
            cur = stack.pop()
            for pred in self._reverse.get(cur, ()):
                if pred not in out:
                    out.add(pred)
                    stack.append(pred)
        return out

    # ------------------------------------------------------------------
    # Order + closure
    # ------------------------------------------------------------------

    def topological_order(self) -> list[Claim]:
        """Kahn's algorithm. Raises CycleError on a cycle."""
        in_degree: dict[str, int] = {
            cid: len(self._reverse.get(cid, ())) for cid in self._nodes
        }
        ready: list[str] = sorted([cid for cid, d in in_degree.items() if d == 0])
        order: list[Claim] = []
        while ready:
            cid = ready.pop(0)
            order.append(self._nodes[cid])
            for succ in sorted(self._forward.get(cid, ())):
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    ready.append(succ)
        if len(order) != len(self._nodes):
            missing = [cid for cid in self._nodes if cid not in {c.claim_id for c in order}]
            raise CycleError(f"cycle in claim DAG involving: {missing[:5]}")
        return order

    def renderable_closure(self, verified_ids: set[str]) -> set[str]:
        """Return ids renderable given a verified-set.

        A claim is renderable iff:
            1. its own id is in `verified_ids`, AND
            2. every id in its `ancestors` is also in `verified_ids`.
        """
        renderable: set[str] = set()
        for cid in self._nodes:
            if cid not in verified_ids:
                continue
            ancestors = self.ancestors(cid)
            if ancestors.issubset(verified_ids):
                renderable.add(cid)
        return renderable

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._nodes)

    def summary(self) -> dict:
        return {
            "num_claims": len(self._nodes),
            "num_edges": len(self.edges()),
            "num_roots": sum(1 for cid in self._nodes if not self._reverse.get(cid)),
            "num_leaves": sum(1 for cid in self._nodes if not self._forward.get(cid)),
        }
