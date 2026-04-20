"""Multi-turn trust propagation.

In multi-step tasks, each turn's output becomes grounding for the next turn.
A single hallucination early in the chain cascades — downstream claims depend
on the upstream assumption. The TrustChain tracks this dependency structure
across turns so we can:
    - report the *first* turn where the chain became unreliable
    - compute `downstream_blast_radius(turn) = # of dependent claims flagged`
    - block or annotate later turns that rely on a blocked prior claim

A chain is just a DAG of (turn_idx, claim_id) nodes with edges representing
"turn B built on turn A's claim C".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass(frozen=True)
class ChainNode:
    """One claim in one turn of a multi-step chain."""
    turn_idx: int
    claim_id: str

    def key(self) -> str:
        return f"t{self.turn_idx}:{self.claim_id}"


@dataclass(frozen=True)
class ChainEdge:
    """Turn `dst` relies on claim `src`."""
    src: ChainNode
    dst: ChainNode


@dataclass
class TrustChain:
    """Directed graph of cross-turn claim dependencies."""

    nodes: dict[str, ChainNode] = field(default_factory=dict)
    edges: list[ChainEdge] = field(default_factory=list)
    turn_verdicts: dict[int, str] = field(default_factory=dict)
    claim_verdicts: dict[str, str] = field(default_factory=dict)
    """claim_id → verdict string (verified/unsupported/contradicted/uncertain)."""

    def add_turn(
        self,
        turn_idx: int,
        claim_ids: Iterable[str],
        parents: Optional[dict[str, list[str]]] = None,
    ) -> None:
        """Register a turn's claims and their parent-turn claim dependencies.

        Args:
            turn_idx: Zero-based turn index.
            claim_ids: IDs of claims emitted this turn.
            parents: For each claim, the list of parent-claim IDs (from any
                     prior turn) it depends on.
        """
        for cid in claim_ids:
            node = ChainNode(turn_idx=turn_idx, claim_id=cid)
            self.nodes[node.key()] = node

        if parents:
            for child_id, parent_ids in parents.items():
                child = self._find_node(turn_idx, child_id)
                if child is None:
                    continue
                for pid in parent_ids:
                    parent = self._find_parent_node(pid, turn_idx)
                    if parent is not None:
                        self.edges.append(ChainEdge(src=parent, dst=child))

    def set_claim_verdict(self, claim_id: str, verdict: str) -> None:
        self.claim_verdicts[claim_id] = verdict

    def set_turn_verdict(self, turn_idx: int, verdict: str) -> None:
        self.turn_verdicts[turn_idx] = verdict

    def first_unreliable_turn(self) -> Optional[int]:
        """Lowest turn index that is not verified (cascade root)."""
        for idx in sorted(self.turn_verdicts.keys()):
            if self.turn_verdicts[idx] not in ("verified",):
                return idx
        return None

    def blast_radius(self, bad_claim_id: str) -> set[str]:
        """All claim_ids transitively dependent on `bad_claim_id`.

        Returns the set of descendant claims (not including bad_claim_id itself).
        """
        descendants: set[str] = set()
        frontier: list[str] = [bad_claim_id]
        while frontier:
            current = frontier.pop()
            for e in self.edges:
                if e.src.claim_id == current and e.dst.claim_id not in descendants:
                    descendants.add(e.dst.claim_id)
                    frontier.append(e.dst.claim_id)
        return descendants

    def cascade_summary(self) -> dict:
        """Top-level chain metrics for the certificate."""
        unreliable_root = self.first_unreliable_turn()
        bad_ids = {
            cid for cid, v in self.claim_verdicts.items()
            if v in ("unsupported", "contradicted")
        }
        total_blast = set()
        for bid in bad_ids:
            total_blast |= self.blast_radius(bid)
        return {
            "turns_total": len(self.turn_verdicts),
            "claims_total": len(self.nodes),
            "edges_total": len(self.edges),
            "first_unreliable_turn": unreliable_root,
            "bad_root_claims": sorted(bad_ids),
            "cascade_blast_radius": len(total_blast),
            "cascade_affected_claims": sorted(total_blast),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_node(self, turn_idx: int, claim_id: str) -> Optional[ChainNode]:
        return self.nodes.get(ChainNode(turn_idx=turn_idx, claim_id=claim_id).key())

    def _find_parent_node(
        self, claim_id: str, child_turn: int
    ) -> Optional[ChainNode]:
        """Parent nodes must be in a strictly earlier turn."""
        for key, node in self.nodes.items():
            if node.claim_id == claim_id and node.turn_idx < child_turn:
                return node
        return None
