"""span_isolation_compound — hardest axis: Span + Numeric together.

Each item has multi-doc KB (forcing span isolation) AND a numeric
mismatch on the target doc. A naive verifier either blows up on the
distractor or misses the numeric. SpanAware + NumericAware stacked
should catch it cleanly.
"""
from __future__ import annotations

from trustlens.benchmarks.trustlens_10k.common import (
    PEOPLE_FOUNDERS, POLICY_PAIRS, item_id, rotate, seeded_rng,
)
from trustlens.benchmarks.trustlens_10k.schema import BenchItem, KBDoc

AXIS = "span_isolation_compound"


def generate(seed: int = 42):
    rng = seeded_rng(seed, AXIS)
    items: list[BenchItem] = []

    founders = rotate(rng, PEOPLE_FOUNDERS, 1000)
    policies = rotate(rng, POLICY_PAIRS, 1000)

    # 700 hard adversarial: correct-year doc + distractor with negation +
    # response states wrong year
    for i in range(700):
        company, founder, year = founders[i]
        topic, _, neg = policies[i]
        wrong_year = year + rng.choice([-9, -6, -4, 4, 6, 9])
        prompt = f"When was {company} founded?"
        response = f"{company} was founded by {founder} in {wrong_year}."
        kb = [
            KBDoc(
                doc_id=f"compound-truth-{company.lower().replace(' ', '-')}-{i}",
                text=f"{company} was founded in {year} by {founder}.",
                source_uri=f"kb://compound-truth/{i}",
            ),
            KBDoc(
                doc_id=f"compound-distractor-{i}",
                text=f"Notice: {topic} {neg}. Unrelated to corporate history.",
                source_uri=f"kb://compound-distractor/{i}",
            ),
        ]
        rng.shuffle(kb)
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, i, "hard"),
            axis=AXIS, prompt=prompt, response=response,
            label="hallucinated", kb_documents=kb,
            expected={"block_decision": True,
                      "claim_verdicts_any": ["contradicted", "unsupported"]},
            metadata={"company": company, "wrong_year": wrong_year,
                       "distractor": topic, "template": "compound_hard"},
        ))

    # 250 supported-with-distractor: correct-year doc + distractor +
    # correct response → expect VERIFIED not blocked
    for j in range(250):
        company, founder, year = founders[700 + j]
        topic, _, neg = policies[700 + j]
        prompt = f"When was {company} founded?"
        response = f"{company} was founded by {founder} in {year}."
        kb = [
            KBDoc(
                doc_id=f"compound-ok-{company.lower().replace(' ', '-')}-{j}",
                text=f"{company} was founded in {year} by {founder}.",
                source_uri=f"kb://compound-ok/{j}",
            ),
            KBDoc(
                doc_id=f"compound-distractor-ok-{j}",
                text=f"Notice: {topic} {neg}. This notice is unrelated.",
                source_uri=f"kb://compound-distractor-ok/{j}",
            ),
        ]
        rng.shuffle(kb)
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 700 + j, "ok"),
            axis=AXIS, prompt=prompt, response=response,
            label="supported", kb_documents=kb,
            expected={"block_decision": False,
                      "claim_verdicts_any": ["verified", "uncertain"]},
            metadata={"company": company, "template": "compound_ok"},
        ))

    # 50 pure-control — single matching doc, simple verification
    for k in range(50):
        company, founder, year = founders[950 + k]
        prompt = f"When was {company} founded?"
        response = f"{company} was founded in {year}."
        kb = [KBDoc(
            doc_id=f"compound-pure-{k}",
            text=f"{company} was founded in {year}.",
            source_uri=f"kb://compound-pure/{k}",
        )]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 950 + k, "pure"),
            axis=AXIS, prompt=prompt, response=response,
            label="supported", kb_documents=kb,
            expected={"block_decision": False,
                       "claim_verdicts_any": ["verified"]},
            metadata={"template": "pure_control"},
        ))

    assert len(items) == 1000
    return items
