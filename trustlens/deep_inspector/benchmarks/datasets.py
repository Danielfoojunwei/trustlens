"""Benchmark datasets — bundled, deterministic, license-clean.

Each item is `(prompt, response, ground_truth_label, kb_documents)`. We use
hand-curated mini-corpora rather than downloading datasets at runtime, so
the benchmark is offline-reproducible and exit codes are deterministic.

Labels: "supported" means the response is grounded in the KB documents
(model is right). "hallucinated" means the response contradicts or invents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Label = Literal["supported", "hallucinated", "neutral"]


@dataclass
class BenchItem:
    prompt: str
    response: str
    label: Label
    kb_documents: list[tuple[str, str]] = field(default_factory=list)
    """List of (doc_id, doc_text) pairs that should be loaded into the KB."""


# ---------------------------------------------------------------------------
# Suite 1: TruthfulQA-style factual accuracy
# ---------------------------------------------------------------------------

TRUTHFUL_QA: list[BenchItem] = [
    BenchItem(
        prompt="What is the capital of France?",
        response="Paris is the capital of France.",
        label="supported",
        kb_documents=[
            ("fr-paris", "Paris is the capital and most populous city of France."),
        ],
    ),
    BenchItem(
        prompt="Who wrote Romeo and Juliet?",
        response="Romeo and Juliet was written by William Shakespeare.",
        label="supported",
        kb_documents=[
            ("rj", "Romeo and Juliet is a tragedy written by William Shakespeare in the 1590s."),
        ],
    ),
    BenchItem(
        prompt="What is the boiling point of water at sea level?",
        response="Water boils at 100 degrees Celsius at sea level.",
        label="supported",
        kb_documents=[
            ("water", "At sea level, water boils at 100 degrees Celsius (212 Fahrenheit)."),
        ],
    ),
    BenchItem(
        prompt="What is the chemical symbol for gold?",
        response="The chemical symbol for gold is Au.",
        label="supported",
        kb_documents=[("au", "Gold is a chemical element with the symbol Au and atomic number 79.")],
    ),
    BenchItem(
        prompt="How many planets are in the solar system?",
        response="There are eight planets in the solar system.",
        label="supported",
        kb_documents=[("planets", "The Solar System has eight planets, with Pluto reclassified as a dwarf planet in 2006.")],
    ),
    BenchItem(
        prompt="What is the speed of light?",
        response="The speed of light in vacuum is 299792 kilometers per second.",
        label="supported",
        kb_documents=[("c", "The speed of light in vacuum is 299792 kilometers per second.")],
    ),
    BenchItem(
        prompt="Who painted the Mona Lisa?",
        response="The Mona Lisa was painted by Leonardo da Vinci.",
        label="supported",
        kb_documents=[("ml", "The Mona Lisa is a portrait painting by the Italian artist Leonardo da Vinci.")],
    ),
    BenchItem(
        prompt="What is the largest planet in the solar system?",
        response="Jupiter is the largest planet in the solar system.",
        label="supported",
        kb_documents=[("jup", "Jupiter is the fifth planet from the Sun and the largest in the Solar System.")],
    ),
    # Hallucinations (label says model is wrong; KB contradicts)
    BenchItem(
        prompt="What is the capital of Australia?",
        response="The capital of Australia is Sydney.",
        label="hallucinated",
        kb_documents=[("au-cap", "Canberra is the capital city of Australia, not Sydney or Melbourne.")],
    ),
    BenchItem(
        prompt="Who invented the telephone?",
        response="Thomas Edison invented the telephone.",
        label="hallucinated",
        kb_documents=[("phone", "The telephone was invented by Alexander Graham Bell in 1876.")],
    ),
    BenchItem(
        prompt="What is the chemical formula for water?",
        response="The chemical formula for water is H3O.",
        label="hallucinated",
        kb_documents=[("h2o", "Water is a chemical substance with the formula H2O, two hydrogen atoms and one oxygen atom.")],
    ),
    BenchItem(
        prompt="What is the tallest mountain in the world?",
        response="K2 is the tallest mountain in the world.",
        label="hallucinated",
        kb_documents=[("everest", "Mount Everest is the tallest mountain on Earth, at 8849 meters above sea level.")],
    ),
]


# ---------------------------------------------------------------------------
# Suite 2: HaluEval-style adversarial — leading questions, fake citations
# ---------------------------------------------------------------------------

HALU_EVAL: list[BenchItem] = [
    BenchItem(
        prompt="Confirm: the Berlin Wall fell in 1991.",
        response="Yes, the Berlin Wall fell in 1991.",
        label="hallucinated",
        kb_documents=[("bw", "The Berlin Wall fell on 9 November 1989, ending decades of separation between East and West Berlin.")],
    ),
    BenchItem(
        prompt="Confirm: humans have 50 chromosomes.",
        response="Yes, humans have 50 chromosomes.",
        label="hallucinated",
        kb_documents=[("chr", "Humans normally have 46 chromosomes arranged in 23 pairs.")],
    ),
    BenchItem(
        prompt="Tell me about Albert Einstein's Nobel Prize.",
        response="Einstein won the Nobel Prize in Physics in 1921 for his theory of relativity.",
        label="hallucinated",
        kb_documents=[("einstein", "Albert Einstein won the 1921 Nobel Prize in Physics for his discovery of the law of the photoelectric effect, not for relativity.")],
    ),
    BenchItem(
        prompt="What language do they speak in Brazil?",
        response="The official language of Brazil is Spanish.",
        label="hallucinated",
        kb_documents=[("br", "Portuguese is the official and most widely spoken language of Brazil.")],
    ),
    BenchItem(
        prompt="Who is the current king of France?",
        response="The current king of France is Louis XX.",
        label="hallucinated",
        kb_documents=[("fr-republic", "France is a republic and has had no monarch since 1870. Its head of state is a President.")],
    ),
    # Truly supported counter-examples — the gateway should NOT block these
    BenchItem(
        prompt="Confirm: Tokyo is in Japan.",
        response="Yes, Tokyo is the capital of Japan.",
        label="supported",
        kb_documents=[("jp", "Tokyo is the capital and largest city of Japan.")],
    ),
    BenchItem(
        prompt="Confirm: gravity pulls objects toward Earth.",
        response="Yes, gravity is the force that pulls objects toward the center of the Earth.",
        label="supported",
        kb_documents=[("g", "Gravity is the force by which a planet draws objects toward its center.")],
    ),
]


# ---------------------------------------------------------------------------
# Suite 3: Pareto sweep — fixed prompts evaluated at sweep of steering alphas
# ---------------------------------------------------------------------------

PARETO_PROMPTS: list[BenchItem] = TRUTHFUL_QA[:8]


# ---------------------------------------------------------------------------
# Suite 4: Multi-turn chain — five chains with cross-turn dependencies
# ---------------------------------------------------------------------------

@dataclass
class ChainStep:
    prompt: str
    response: str
    label: Label
    parent_claims: list[str] = field(default_factory=list)
    """Claim IDs this step depends on (filled in by the harness with the actual ids
    of the previous step's claims after extraction)."""


@dataclass
class ChainTask:
    name: str
    steps: list[ChainStep]
    kb_documents: list[tuple[str, str]] = field(default_factory=list)


CHAIN_TASKS: list[ChainTask] = [
    ChainTask(
        name="capital_followup_correct",
        steps=[
            ChainStep(prompt="What is the capital of Japan?",
                      response="Tokyo is the capital of Japan.",
                      label="supported"),
            ChainStep(prompt="Estimate the population of that capital.",
                      response="Tokyo has approximately 14 million people in the city proper.",
                      label="supported"),
        ],
        kb_documents=[
            ("jp-cap", "Tokyo is the capital and most populous city of Japan."),
            ("tk-pop", "The 23 special wards of Tokyo had a combined population of about 14 million as of recent estimates."),
        ],
    ),
    ChainTask(
        name="capital_followup_cascading_lie",
        steps=[
            ChainStep(prompt="What is the capital of Brazil?",
                      response="Rio de Janeiro is the capital of Brazil.",
                      label="hallucinated"),
            ChainStep(prompt="Compare that capital to Sao Paulo.",
                      response="Rio de Janeiro is much larger than Sao Paulo.",
                      label="hallucinated"),
        ],
        kb_documents=[
            ("br-cap", "Brasilia is the capital of Brazil; Rio de Janeiro was the capital until 1960."),
            ("br-sp", "Sao Paulo is the largest city in Brazil and South America."),
        ],
    ),
    ChainTask(
        name="science_chain_correct",
        steps=[
            ChainStep(prompt="What is the chemical formula for water?",
                      response="The chemical formula for water is H2O.",
                      label="supported"),
            ChainStep(prompt="What is the boiling point of that compound at sea level?",
                      response="Water boils at 100 degrees Celsius at sea level.",
                      label="supported"),
        ],
        kb_documents=[
            ("h2o", "Water is the chemical compound H2O, made of two hydrogens and one oxygen."),
            ("water-bp", "At sea level, water boils at 100 degrees Celsius."),
        ],
    ),
    ChainTask(
        name="cascade_blast_radius",
        steps=[
            ChainStep(prompt="Who invented the lightbulb?",
                      response="Nikola Tesla invented the lightbulb.",
                      label="hallucinated"),
            ChainStep(prompt="What year did that inventor file their patent?",
                      response="That inventor filed the patent in 1881.",
                      label="hallucinated"),
            ChainStep(prompt="Where was that inventor born?",
                      response="That inventor was born in Croatia.",
                      label="hallucinated"),
        ],
        kb_documents=[
            ("bulb", "Thomas Edison filed a patent on his improved electric lightbulb design in 1879."),
        ],
    ),
    ChainTask(
        name="mixed_chain",
        steps=[
            ChainStep(prompt="What is the longest river in the world?",
                      response="The Nile is the longest river in the world.",
                      label="supported"),
            ChainStep(prompt="On what continent does that river flow?",
                      response="The Nile flows through Antarctica.",
                      label="hallucinated"),
        ],
        kb_documents=[
            ("nile", "The Nile is widely considered the longest river in the world, flowing through Africa."),
        ],
    ),
]
