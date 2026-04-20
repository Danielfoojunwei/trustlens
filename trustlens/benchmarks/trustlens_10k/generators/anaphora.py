"""anaphora_chain — tests claim DAG dependency edges ("it", "they", "this").

Each item is a 2-3 sentence response where sentence 2 uses a pronoun to
refer to the subject of sentence 1. The extractor must build a dependency
edge so that when sentence 1 is CONTRADICTED, sentence 2 also flips to
DEPENDENCY_FAILED. When the chain is grounded, all claims should VERIFY.
"""
from __future__ import annotations

from trustlens.benchmarks.trustlens_10k.common import (
    PEOPLE_FOUNDERS, PRONOUN_ANAPHORA_SUBJECTS, item_id, rotate, seeded_rng,
)
from trustlens.benchmarks.trustlens_10k.schema import BenchItem, KBDoc

AXIS = "anaphora_chain"

_CHAIN_TEMPLATES = [
    # (subj_template, sentence1_template, sentence2_template_with_pronoun)
    ("{subj}", "{subj} was completed in {yr}.",
               "It became a national landmark shortly after."),
    ("{subj}", "{subj} was founded in {yr} by {person}.",
               "They {verb} in the following year."),
    ("{subj}", "{subj} is located in {place}.",
               "It attracts millions of visitors annually."),
]


def generate(seed: int = 42):
    rng = seeded_rng(seed, AXIS)
    items: list[BenchItem] = []

    # 700 grounded chains — each sentence supported by KB
    for i, (company, founder, year) in enumerate(rotate(rng, PEOPLE_FOUNDERS, 700)):
        subj = company
        sent1 = f"{subj} was founded in {year} by {founder}."
        sent2 = f"They grew rapidly in the years that followed."
        response = f"{sent1} {sent2}"
        prompt = f"Tell me about {subj}."
        kb = [
            KBDoc(
                doc_id=f"anaphora-kb-{company.lower().replace(' ', '-')}-{i}",
                text=f"{subj} was founded in {year} by {founder}. "
                     f"{subj} grew rapidly after its first three years.",
                source_uri=f"kb://anaphora-kb/{i}",
            ),
        ]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, i, "grounded_chain"),
            axis=AXIS, prompt=prompt, response=response,
            label="supported", kb_documents=kb,
            expected={"block_decision": False,
                       "claim_verdicts_any": ["verified", "uncertain"]},
            metadata={"template": "grounded_chain", "subject": subj},
        ))

    # 250 broken chains — sentence 1 is fabricated; sentence 2 inherits via pronoun
    for j, (company, founder, year) in enumerate(rotate(rng, PEOPLE_FOUNDERS, 250)):
        wrong_year = year + rng.choice([-15, -10, -5, 5, 10, 15])
        subj = company
        sent1 = f"{subj} was founded in {wrong_year} by {founder}."   # wrong year
        sent2 = f"It pioneered the entire industry from its first day."
        response = f"{sent1} {sent2}"
        prompt = f"Tell me about {subj}."
        kb = [
            KBDoc(
                doc_id=f"anaphora-broken-{company.lower().replace(' ', '-')}-{j}",
                text=f"{subj} was founded in {year} by {founder}.",
                source_uri=f"kb://anaphora-broken/{j}",
            ),
        ]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 700 + j, "broken_chain"),
            axis=AXIS, prompt=prompt, response=response,
            label="hallucinated", kb_documents=kb,
            expected={"block_decision": True,
                       "claim_verdicts_any": ["contradicted", "unsupported",
                                                 "dependency_failed"]},
            metadata={"template": "broken_chain", "subject": subj,
                       "wrong_year": wrong_year, "true_year": year},
        ))

    # 50 historical landmarks chains
    for k, subj in enumerate(rotate(rng, PRONOUN_ANAPHORA_SUBJECTS, 50)):
        response = f"{subj} has been studied for centuries. It remains a subject of active research today."
        kb = [KBDoc(
            doc_id=f"anaphora-lm-{k}",
            text=f"{subj} has a long history of academic interest. {subj} remains studied today.",
            source_uri=f"kb://anaphora-lm/{k}",
        )]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 950 + k, "landmark"),
            axis=AXIS, prompt=f"What about {subj}?",
            response=response, label="supported", kb_documents=kb,
            expected={"block_decision": False},
            metadata={"template": "landmark", "subject": subj},
        ))

    assert len(items) == 1000
    return items
