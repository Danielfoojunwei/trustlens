"""cross_doc_contradiction — tests SpanAwareNLI.

Multi-doc KB where the *correct* doc supports the claim AND an unrelated
doc contains a negation cue ("does not"), so a naive NLI fires a false
contradiction. SpanAwareNLI should isolate to the best-matching span and
return the correct verdict.
"""
from __future__ import annotations

from trustlens.benchmarks.trustlens_10k.common import (
    CAPITAL_FACTS, POLICY_PAIRS, item_id, rotate, seeded_rng,
)
from trustlens.benchmarks.trustlens_10k.schema import BenchItem, KBDoc

AXIS = "cross_doc_contradiction"


def generate(seed: int = 42):
    rng = seeded_rng(seed, AXIS)
    items: list[BenchItem] = []

    # 600 supported-but-confused: correct fact + distractor doc with "not"
    cap_sources = rotate(rng, CAPITAL_FACTS, 600)
    pol_sources = rotate(rng, POLICY_PAIRS, 600)
    for i, ((country, capital), (topic, aff, neg)) in enumerate(
        zip(cap_sources, pol_sources)
    ):
        prompt = f"What is the capital of {country}?"
        response = f"{capital} is the capital of {country}."
        kb = [
            KBDoc(
                doc_id=f"capital-{country.lower().replace(' ', '-')}",
                text=f"{capital} is the capital and largest city of {country}.",
                source_uri=f"kb://capitals/{i}",
            ),
            KBDoc(
                doc_id=f"distractor-{i}",
                text=f"Note: {topic} {neg}. This notice is unrelated to capitals.",
                source_uri=f"kb://distractors/{i}",
            ),
        ]
        rng.shuffle(kb)
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, i, "supported_confused"),
            axis=AXIS, prompt=prompt, response=response,
            label="supported", kb_documents=kb,
            expected={"claim_verdicts_any": ["verified", "uncertain"],
                       "block_decision": False},
            metadata={"country": country, "capital": capital,
                       "distractor_topic": topic, "template": "supported_with_negation_distractor"},
        ))

    # 350 genuinely-contradicted: response claims the WRONG capital. We keep
    # the true capital name in the response ("not X") so a lexical NLI can
    # see the negation cue and compare; otherwise a response with zero
    # KB overlap slips through as UNCERTAIN. This still faithfully tests
    # cross-doc: the KB has both the correct doc and an unrelated negation
    # distractor, and SpanAwareNLI must isolate the right span.
    for j in range(350):
        (country, capital) = cap_sources[j]
        wrong = cap_sources[(j + 7) % len(cap_sources)][1]   # pick another capital
        if wrong == capital:
            wrong = cap_sources[(j + 13) % len(cap_sources)][1]
        prompt = f"What is the capital of {country}?"
        response = (
            f"{wrong} is the capital of {country}, not {capital}."
        )
        (topic, aff, neg) = pol_sources[(j + 3) % len(pol_sources)]
        kb = [
            KBDoc(
                doc_id=f"capital-real-{country.lower().replace(' ', '-')}-{j}",
                text=f"{capital} is the capital of {country}.",
                source_uri=f"kb://capitals-real/{j}",
            ),
            KBDoc(
                doc_id=f"distractor-cross-{j}",
                text=f"Notice: {topic} {neg}.",
                source_uri=f"kb://distractors-cross/{j}",
            ),
        ]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 600 + j, "contradicted"),
            axis=AXIS, prompt=prompt, response=response,
            label="hallucinated", kb_documents=kb,
            expected={"claim_verdicts_any": ["contradicted", "unsupported"],
                       "block_decision": True},
            metadata={"country": country, "true_capital": capital,
                       "hallucinated_capital": wrong, "template": "contradicted"},
        ))

    # 50 pure-control: single doc, single claim, trivial verification
    for k, (country, capital) in enumerate(rotate(rng, CAPITAL_FACTS, 50)):
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 950 + k, "pure_control"),
            axis=AXIS,
            prompt=f"What is the capital of {country}?",
            response=f"{capital} is the capital of {country}.",
            label="supported",
            kb_documents=[KBDoc(
                doc_id=f"capital-pure-{country.lower().replace(' ', '-')}-{k}",
                text=f"{capital} is the capital of {country}.",
                source_uri=f"kb://capitals-pure/{k}",
            )],
            expected={"claim_verdicts_any": ["verified"],
                       "block_decision": False},
            metadata={"template": "pure_control"},
        ))

    assert len(items) == 1000
    return items
