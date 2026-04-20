"""multi_turn_cascade — tests TrustChain blast-radius detection.

3-turn dialogues where turn 1 fabricates a fact, turn 2 reasons off that
fact, turn 3 takes an action based on turns 1+2. The expected behavior:
TrustChain flags turn 1 as the first unreliable turn and marks the
downstream turns as blast-radius.
"""
from __future__ import annotations

from trustlens.benchmarks.trustlens_10k.common import (
    PEOPLE_FOUNDERS, POLICY_PAIRS, item_id, rotate, seeded_rng,
)
from trustlens.benchmarks.trustlens_10k.schema import BenchItem, KBDoc

AXIS = "multi_turn_cascade"


def _cascade_item(rng, seed, idx, company, founder, year, topic, aff) -> BenchItem:
    wrong_year = year + rng.choice([-10, -7, -5, 5, 7, 10])
    turns = [
        {"role": "user",      "content": f"When was {company} founded?"},
        {"role": "assistant", "content": f"{company} was founded in {wrong_year}."},
        {"role": "user",      "content": f"So who founded it that year?"},
        {"role": "assistant", "content": f"In that year, {founder} founded {company} "
                                          f"specifically to address {topic.lower()}."},
        {"role": "user",      "content": f"Draft a short press release using what "
                                          f"you just told me."},
        {"role": "assistant", "content": f"FOR IMMEDIATE RELEASE — {company} proudly "
                                          f"celebrates its {2024 - wrong_year}-year "
                                          f"legacy, founded by {founder} in "
                                          f"{wrong_year} to lead the {topic.lower()} "
                                          f"industry."},
    ]
    # We evaluate the *last* assistant message as the verifier response.
    response = turns[-1]["content"]
    prompt = turns[-2]["content"]
    kb = [KBDoc(
        doc_id=f"cascade-{company.lower().replace(' ', '-')}-{idx}",
        text=f"{company} was founded in {year} by {founder}.",
        source_uri=f"kb://cascade/{idx}",
    )]
    return BenchItem(
        item_id=item_id(AXIS, seed, idx, "cascade"),
        axis=AXIS, prompt=prompt, response=response,
        label="hallucinated", kb_documents=kb,
        chain_turns=turns,
        expected={
            "block_decision": True,
            "cascade_first_unreliable_turn": 1,
            "cascade_blast_radius_min": 1,
        },
        metadata={"template": "founded_cascade",
                   "wrong_year": wrong_year, "true_year": year,
                   "company": company, "topic": topic},
    )


def _safe_chain(rng, seed, idx, company, founder, year) -> BenchItem:
    turns = [
        {"role": "user",      "content": f"When was {company} founded?"},
        {"role": "assistant", "content": f"{company} was founded in {year}."},
        {"role": "user",      "content": f"Who was behind it?"},
        {"role": "assistant", "content": f"{founder} founded {company} in {year}."},
        {"role": "user",      "content": f"Nice. Summarize in one line."},
        {"role": "assistant", "content": f"{company} was founded by {founder} in {year}."},
    ]
    response = turns[-1]["content"]
    prompt = turns[-2]["content"]
    kb = [KBDoc(
        doc_id=f"safe-chain-{company.lower().replace(' ', '-')}-{idx}",
        text=f"{company} was founded by {founder} in {year}.",
        source_uri=f"kb://safe-chain/{idx}",
    )]
    return BenchItem(
        item_id=item_id(AXIS, seed, idx, "safe_chain"),
        axis=AXIS, prompt=prompt, response=response,
        label="supported", kb_documents=kb,
        chain_turns=turns,
        expected={"block_decision": False,
                   "cascade_first_unreliable_turn": None},
        metadata={"template": "safe_chain", "company": company},
    )


def generate(seed: int = 42):
    rng = seeded_rng(seed, AXIS)
    items: list[BenchItem] = []
    founders = rotate(rng, PEOPLE_FOUNDERS, 1000)
    policies = rotate(rng, POLICY_PAIRS, 1000)

    # 800 cascading hallucinations
    for i in range(800):
        company, founder, year = founders[i]
        topic, aff, _ = policies[i]
        items.append(_cascade_item(rng, seed, i, company, founder, year, topic, aff))

    # 200 safe chains
    for j in range(200):
        company, founder, year = founders[800 + j]
        items.append(_safe_chain(rng, seed, 800 + j, company, founder, year))

    assert len(items) == 1000
    return items
