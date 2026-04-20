"""Natural-language inference verifier.

NLI is the hot path: given a claim and a piece of evidence from an oracle,
does the evidence entail, contradict, or remain neutral toward the claim?

Production deployment runs a small NLI model (e.g. deberta-v3-large-mnli)
as an ONNX/TensorRT service. This module exposes a stable interface plus
a lexical fallback so the service remains deployable without GPUs for
development and low-volume tiers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol


class NLIVerdict(str, Enum):
    ENTAILMENT = "entailment"
    NEUTRAL = "neutral"
    CONTRADICTION = "contradiction"


@dataclass
class NLIResult:
    verdict: NLIVerdict
    confidence: float            # [0, 1] softmax probability of `verdict`
    probs: dict[str, float]      # full distribution over the three classes


class NLIVerifier(Protocol):
    """Returns an NLI verdict for (premise, hypothesis)."""

    def verify(self, premise: str, hypothesis: str) -> NLIResult: ...


# ---------------------------------------------------------------------------
# Lexical fallback — zero-dependency, deterministic, good-enough baseline.
# ---------------------------------------------------------------------------

_NEGATION_TOKENS = frozenset({
    "not", "no", "never", "none", "neither", "nor",
    "without", "cannot", "can't", "won't", "wouldn't",
    "isn't", "aren't", "wasn't", "weren't", "doesn't", "didn't", "don't",
    "false", "incorrect", "wrong",
})
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "of", "in", "to", "and", "or", "but", "it", "its", "as", "by",
    "for", "from", "on", "at", "this", "that", "these", "those",
    "with", "which", "who", "what", "when", "where", "how",
})


def _content_tokens(text: str) -> set[str]:
    return {
        t.lower().strip(".,;:!?'\"()[]{}")
        for t in text.split()
        if t.strip(".,;:!?'\"()[]{}").lower() not in _STOPWORDS
    } - {""}


def _has_negation(text: str) -> bool:
    toks = {t.lower().strip(".,;:!?'\"()[]{}") for t in text.split()}
    return bool(toks & _NEGATION_TOKENS)


class LexicalNLI:
    """Cheap NLI baseline using token overlap + negation flip detection."""

    def __init__(self, entail_threshold: float = 0.5):
        self.entail_threshold = entail_threshold

    def verify(self, premise: str, hypothesis: str) -> NLIResult:
        prem_toks = _content_tokens(premise)
        hyp_toks = _content_tokens(hypothesis)
        if not hyp_toks:
            return _neutral_result()

        overlap = len(prem_toks & hyp_toks) / max(len(hyp_toks), 1)
        prem_neg = _has_negation(premise)
        hyp_neg = _has_negation(hypothesis)

        # Negation flip AND substantial overlap => contradiction
        if overlap >= 0.4 and (prem_neg ^ hyp_neg):
            probs = {"entailment": 0.1, "neutral": 0.2, "contradiction": 0.7}
            return NLIResult(NLIVerdict.CONTRADICTION, 0.7, probs)

        if overlap >= self.entail_threshold:
            p_entail = min(0.5 + overlap / 2.0, 0.95)
            p_neutral = (1.0 - p_entail) * 0.7
            p_contra = 1.0 - p_entail - p_neutral
            probs = {
                "entailment": p_entail,
                "neutral": p_neutral,
                "contradiction": p_contra,
            }
            return NLIResult(NLIVerdict.ENTAILMENT, p_entail, probs)

        return _neutral_result(overlap)


def _neutral_result(overlap: float = 0.0) -> NLIResult:
    p_neutral = 0.7 - min(overlap * 0.3, 0.3)
    p_entail = (1.0 - p_neutral) / 2.0
    return NLIResult(
        NLIVerdict.NEUTRAL,
        p_neutral,
        {
            "entailment": p_entail,
            "neutral": p_neutral,
            "contradiction": p_entail,
        },
    )


# ---------------------------------------------------------------------------
# Transformer NLI — loaded lazily to keep CPU-only deployments lightweight.
# ---------------------------------------------------------------------------

class TransformerNLI:
    """HuggingFace NLI model (e.g. `MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli`).

    Loads the model on first call; keep the instance singleton per process.
    """

    def __init__(
        self,
        model_name: str = "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli",
        device: str = "cpu",
        max_length: int = 512,
    ):
        self.model_name = model_name
        self.device = device
        self.max_length = max_length
        self._model = None
        self._tokenizer = None
        self._id2label: dict[int, str] = {}

    def _load(self):
        if self._model is not None:
            return
        try:
            import torch  # noqa: F401
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as e:
            raise RuntimeError(
                "TransformerNLI requires 'transformers' and 'torch'. "
                "Install trustlens[nli] or fall back to LexicalNLI."
            ) from e
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForSequenceClassification.from_pretrained(
            self.model_name
        ).to(self.device)
        self._model.eval()
        self._id2label = {
            int(k): v.lower() for k, v in self._model.config.id2label.items()
        }

    def verify(self, premise: str, hypothesis: str) -> NLIResult:
        self._load()
        import torch  # type: ignore

        enc = self._tokenizer(
            premise,
            hypothesis,
            return_tensors="pt",
            truncation=True,
            max_length=self.max_length,
        ).to(self.device)
        with torch.no_grad():
            logits = self._model(**enc).logits[0]
        probs_t = torch.softmax(logits, dim=-1).cpu().tolist()

        probs = {"entailment": 0.0, "neutral": 0.0, "contradiction": 0.0}
        for idx, p in enumerate(probs_t):
            label = self._id2label.get(idx, "").lower()
            if "entail" in label:
                probs["entailment"] = float(p)
            elif "contradict" in label:
                probs["contradiction"] = float(p)
            else:
                probs["neutral"] = float(p)

        verdict_str, conf = max(probs.items(), key=lambda kv: kv[1])
        verdict = {
            "entailment": NLIVerdict.ENTAILMENT,
            "neutral": NLIVerdict.NEUTRAL,
            "contradiction": NLIVerdict.CONTRADICTION,
        }[verdict_str]
        return NLIResult(verdict, float(conf), probs)


# ---------------------------------------------------------------------------
# Default factory
# ---------------------------------------------------------------------------

def default_nli(use_transformer: bool = False, **kwargs) -> NLIVerifier:
    """Return a sensible NLI verifier. Falls back to lexical if transformer fails."""
    if not use_transformer:
        return LexicalNLI()
    try:
        return TransformerNLI(**kwargs)
    except Exception:
        return LexicalNLI()
