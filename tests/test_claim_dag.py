"""Claim DAG tests — topological order, renderable closure, cycle detection."""

from __future__ import annotations

import pytest

from trustlens.verifier.claim_dag import Claim, ClaimDAG, CycleError, stable_claim_id
from trustlens.verifier.extractor import RegexExtractor


def test_stable_claim_id_is_deterministic() -> None:
    a = stable_claim_id("Paris is the capital of France.", [])
    b = stable_claim_id("Paris is the capital of France.", [])
    assert a == b
    assert stable_claim_id("Paris is the capital of France.", ["c_x"]) != a


def test_topological_order_simple_chain() -> None:
    dag = ClaimDAG()
    a = Claim.create("First.", [])
    b = Claim.create("Second depends on it.", [a.claim_id])
    c = Claim.create("Third depends on the second.", [b.claim_id])
    for cl in [c, b, a]:  # add in reverse to verify ordering
        dag.add(cl)
    order = [n.claim_id for n in dag.topological_order()]
    assert order.index(a.claim_id) < order.index(b.claim_id)
    assert order.index(b.claim_id) < order.index(c.claim_id)


def test_cycle_raises() -> None:
    dag = ClaimDAG()
    # We can't construct a real cycle via Claim.create (depends_on must
    # reference existing ids), but we can hand-craft.
    a = Claim(claim_id="c_a", text="A", depends_on=["c_b"])
    b = Claim(claim_id="c_b", text="B", depends_on=["c_a"])
    dag.add(a)
    dag.add(b)
    with pytest.raises(CycleError):
        dag.topological_order()


def test_renderable_closure_blocks_cascade() -> None:
    dag = ClaimDAG()
    a = Claim.create("Root claim.", [])
    b = Claim.create("Depends on root.", [a.claim_id])
    c = Claim.create("Independent claim.", [])
    for cl in [a, b, c]:
        dag.add(cl)
    # If only b and c are verified (not a), b is not renderable, c is.
    renderable = dag.renderable_closure({b.claim_id, c.claim_id})
    assert c.claim_id in renderable
    assert b.claim_id not in renderable
    # If a, b, c are verified, all are renderable.
    renderable = dag.renderable_closure({a.claim_id, b.claim_id, c.claim_id})
    assert renderable == {a.claim_id, b.claim_id, c.claim_id}


def test_extractor_creates_anaphora_dependency() -> None:
    text = (
        "Brazil is in South America. "
        "The longest river on this continent is the Amazon."
    )
    claims = RegexExtractor().extract(text)
    assert len(claims) >= 2
    # Second claim should depend on the first (anaphora "this continent")
    assert claims[0].claim_id in claims[1].depends_on


def test_idempotent_add() -> None:
    dag = ClaimDAG()
    c = Claim.create("Same content.", [])
    dag.add(c)
    dag.add(c)  # no error
    assert len(dag) == 1
