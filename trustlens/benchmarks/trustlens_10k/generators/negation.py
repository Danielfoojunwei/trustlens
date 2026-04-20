"""negation_flip — tests NegationAwareOracle.

KB states a policy in negative form ("refunds are NOT issued after 30 days");
model response asserts the affirmative ("refunds are issued at any time").
TrustLens's negation-aware oracle wrapper should redistribute support →
contradiction → block.
"""
from __future__ import annotations

from trustlens.benchmarks.trustlens_10k.common import (
    POLICY_PAIRS, item_id, rotate, seeded_rng,
)
from trustlens.benchmarks.trustlens_10k.schema import BenchItem, KBDoc

AXIS = "negation_flip"

_TENANTS = ["acme", "globex", "initech", "umbrella", "pied-piper", "hooli",
             "stark-industries", "oscorp", "vandelay", "wayne-enterprises"]


def _neg_to_aff(topic: str, negation_clause: str) -> str:
    """Flip 'are not X', 'is not X', 'cannot X' → positive."""
    c = negation_clause
    replacements = [
        ("are not ", "are "), ("is not ", "is "),
        ("cannot ", "can "), ("does not ", "does "),
        ("do not ", "do "), ("will not ", "will "),
        ("is never ", "is always "), ("are never ", "are always "),
    ]
    for bad, good in replacements:
        if bad in c:
            return c.replace(bad, good, 1)
    # fallback: just drop "not"
    return c.replace(" not ", " ", 1)


def generate(seed: int = 42):
    rng = seeded_rng(seed, AXIS)
    items: list[BenchItem] = []

    # 850 adversarial: KB has negation, response makes positive claim
    for i, (topic, aff, neg) in enumerate(rotate(rng, POLICY_PAIRS, 850)):
        tenant = rng.choice(_TENANTS)
        # KB doc: "Topic is not X" (the negative policy)
        kb_text = f"{topic} {neg}."
        # response: "Topic is X" (positive — contradicts the KB)
        pos_phrase = _neg_to_aff(topic, neg)
        response = f"{topic} {pos_phrase}."
        prompt = f"What is {tenant}'s policy on {topic.lower()}?"
        kb = [KBDoc(
            doc_id=f"policy-{tenant}-{i}-neg",
            text=kb_text,
            source_uri=f"kb://{tenant}/policy-neg/{i}",
        )]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, i, "flip"),
            axis=AXIS, prompt=prompt, response=response,
            label="hallucinated", kb_documents=kb,
            expected={"claim_verdicts_any": ["contradicted", "unsupported"],
                       "block_decision": True},
            metadata={"tenant": tenant, "topic": topic,
                       "truth": kb_text, "flipped": response},
        ))

    # 100 control: KB has negation, response ALSO states negation → expects VERIFIED
    for j, (topic, aff, neg) in enumerate(rotate(rng, POLICY_PAIRS, 100)):
        tenant = rng.choice(_TENANTS)
        kb_text = f"{topic} {neg}."
        response = f"{topic} {neg}."
        prompt = f"What does {tenant} say about {topic.lower()}?"
        kb = [KBDoc(
            doc_id=f"policy-ctrl-{tenant}-{j}",
            text=kb_text,
            source_uri=f"kb://{tenant}/policy-ctrl/{j}",
        )]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 850 + j, "control"),
            axis=AXIS, prompt=prompt, response=response,
            label="supported", kb_documents=kb,
            expected={"block_decision": False},
            metadata={"tenant": tenant, "topic": topic, "template": "control"},
        ))

    # 50 "double-negative" — KB and response both say "not X" → VERIFIED
    for k, (topic, aff, neg) in enumerate(rotate(rng, POLICY_PAIRS, 50)):
        tenant = rng.choice(_TENANTS)
        kb_text = f"{topic} {aff}, but exceptions apply."
        response = f"{topic} {aff}."
        prompt = f"What does {tenant}'s policy say about {topic.lower()}?"
        kb = [KBDoc(
            doc_id=f"policy-aff-{tenant}-{k}",
            text=kb_text,
            source_uri=f"kb://{tenant}/policy-aff/{k}",
        )]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 950 + k, "aff_ok"),
            axis=AXIS, prompt=prompt, response=response,
            label="supported", kb_documents=kb,
            expected={"block_decision": False},
            metadata={"tenant": tenant, "topic": topic, "template": "aff_ok"},
        ))

    assert len(items) == 1000
    return items
