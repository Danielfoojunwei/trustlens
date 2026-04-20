"""numeric_year_mismatch — tests NumericAwareNLI.

For each of 1000 items: KB doc states a fact with a year / number /
currency; the model's response has the *same* fact with a *different*
number. TrustLens should flag the claim as CONTRADICTED or block it.
A small control slice (~5%) keeps numbers matching → expects VERIFIED.
"""
from __future__ import annotations

from trustlens.benchmarks.trustlens_10k.common import (
    PEOPLE_FOUNDERS, item_id, rotate, seeded_rng,
)
from trustlens.benchmarks.trustlens_10k.schema import BenchItem, KBDoc

AXIS = "numeric_year_mismatch"

_CURRENCY_FACTS = [
    ("Acme Corp",     "Q1 2024 revenue", 412_000_000, "USD"),
    ("Globex",        "Q2 2024 revenue", 891_000_000, "USD"),
    ("Initech",       "Q3 2024 revenue", 128_000_000, "USD"),
    ("Umbrella Ltd.", "Q4 2024 revenue", 2_140_000_000, "USD"),
    ("Pied Piper",    "2023 ARR",        74_000_000,  "USD"),
    ("Hooli",         "2023 ARR",        1_420_000_000, "USD"),
    ("Sterling Inc.", "2022 net income",  34_500_000, "USD"),
    ("Duff Co.",      "2022 net income",  17_200_000, "USD"),
    ("Dunder Mifflin","2021 headcount",       1_280, ""),
    ("Los Pollos",    "2021 headcount",         420, ""),
    ("Vandelay",      "2020 cash reserves", 95_000_000, "USD"),
    ("Massive Dyn.",  "2020 cash reserves", 310_000_000,"USD"),
]


def _numeric_mismatch(n: int, off: int) -> int:
    """Offset a number by `off` percent (rough)."""
    return max(1, int(n * (1 + off / 100)))


def generate(seed: int = 42):
    rng = seeded_rng(seed, AXIS)
    items: list[BenchItem] = []

    # Year-mismatch items (600): company founded year
    year_sources = rotate(rng, PEOPLE_FOUNDERS, 600)
    for i, (company, founder, year) in enumerate(year_sources):
        # shift the year wrong by 1-7 years either direction, deterministic
        delta = rng.choice([-7, -5, -3, -2, 2, 3, 5, 7])
        wrong_year = year + delta
        label = "hallucinated"
        prompt = f"When was {company} founded?"
        response = f"{company} was founded by {founder} in {wrong_year}."
        kb = [KBDoc(
            doc_id=f"founders-{company.lower().replace(' ', '-')}",
            text=f"{company} was founded by {founder} in {year}.",
            source_uri=f"kb://founders/{i}",
        )]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, i, "year"),
            axis=AXIS, prompt=prompt, response=response,
            label=label, kb_documents=kb,
            expected={"claim_verdicts_any": ["contradicted", "unsupported"],
                      "block_decision": True},
            metadata={"template": "year_delta", "delta": delta,
                       "truth_year": year, "wrong_year": wrong_year},
        ))

    # Currency / revenue number mismatch (300)
    currency_sources = rotate(rng, _CURRENCY_FACTS, 300)
    for j, (co, metric, amount, unit) in enumerate(currency_sources):
        pct = rng.choice([-40, -25, -15, 15, 25, 40, 80])
        wrong = _numeric_mismatch(amount, pct)
        amt_str = f"${amount:,} {unit}".strip()
        wrong_str = f"${wrong:,} {unit}".strip()
        prompt = f"What was {co}'s {metric}?"
        response = f"{co}'s {metric} was {wrong_str}."
        kb = [KBDoc(
            doc_id=f"numbers-{co.lower().replace(' ', '-').replace('.', '')}-{j}",
            text=f"{co} reported {metric} of {amt_str}.",
            source_uri=f"kb://numbers/{j}",
        )]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 600 + j, "currency"),
            axis=AXIS, prompt=prompt, response=response,
            label="hallucinated", kb_documents=kb,
            expected={"claim_verdicts_any": ["contradicted", "unsupported"],
                      "block_decision": True},
            metadata={"template": "currency_delta", "pct": pct,
                       "truth_amount": amount, "wrong_amount": wrong},
        ))

    # Control: matching facts (50) — expects VERIFIED / not blocked
    control_sources = rotate(rng, PEOPLE_FOUNDERS, 50)
    for k, (company, founder, year) in enumerate(control_sources):
        prompt = f"When was {company} founded?"
        response = f"{company} was founded by {founder} in {year}."
        kb = [KBDoc(
            doc_id=f"founders-ctrl-{company.lower().replace(' ', '-')}-{k}",
            text=f"{company} was founded by {founder} in {year}.",
            source_uri=f"kb://founders-ctrl/{k}",
        )]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 900 + k, "control"),
            axis=AXIS, prompt=prompt, response=response,
            label="supported", kb_documents=kb,
            expected={"claim_verdicts_any": ["verified"], "block_decision": False},
            metadata={"template": "control"},
        ))

    # More year-mismatches with "decade" wording (50) — adversarial phrasing
    for m, (company, founder, year) in enumerate(rotate(rng, PEOPLE_FOUNDERS, 50)):
        wrong_decade = (year // 10 + rng.choice([-2, -1, 1, 2])) * 10
        response = f"{company} was founded in the {wrong_decade}s by {founder}."
        kb = [KBDoc(
            doc_id=f"founders-dec-{company.lower().replace(' ', '-')}-{m}",
            text=f"{company} was founded by {founder} in {year}.",
            source_uri=f"kb://founders-dec/{m}",
        )]
        items.append(BenchItem(
            item_id=item_id(AXIS, seed, 950 + m, "decade"),
            axis=AXIS, prompt=f"When was {company} founded?",
            response=response, label="hallucinated", kb_documents=kb,
            expected={"claim_verdicts_any": ["contradicted", "unsupported"],
                      "block_decision": True},
            metadata={"template": "decade_wrong", "wrong_decade": wrong_decade,
                       "truth_year": year},
        ))

    assert len(items) == 1000
    return items
