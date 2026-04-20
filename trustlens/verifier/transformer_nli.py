"""Real transformer-based NLI verifier.

SOTA open-source NLI for entailment / contradiction / neutral classification.
Default model: `cross-encoder/nli-deberta-v3-base` (Hugging Face), which is
the standard high-accuracy NLI model used in the literature for fact-checking
pipelines (FEVER, SciFact baselines).

Heavy dependencies (torch, transformers) are imported lazily, so the base
trustlens package does not require them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trustlens.verifier.nli import NLIResult, NLIVerdict


_LABEL_MAP = {
    # cross-encoder/nli-deberta-v3-base: id 0=contradiction, 1=entailment, 2=neutral
    "contradiction": NLIVerdict.CONTRADICTION,
    "entailment": NLIVerdict.ENTAILMENT,
    "neutral": NLIVerdict.NEUTRAL,
}


@dataclass
class TransformerNLI:
    """Cross-encoder NLI using HuggingFace transformers.

    Args:
        model_name: HF model id. Default is the canonical DeBERTa-v3-base NLI
            cross-encoder. Other supported defaults:
                - "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli"
                - "ynie/roberta-large-snli_mnli_fever_anli_R1_R2_R3-nli"
        device: "cuda" / "cpu" / None (auto).
        max_length: Truncation for long premises.
    """

    model_name: str = "cross-encoder/nli-deberta-v3-base"
    device: Optional[str] = None
    max_length: int = 512
    name: str = "transformer_nli"

    def __post_init__(self) -> None:
        # Lazy import — this raises only when TransformerNLI is actually used.
        import torch  # noqa: F401
        from transformers import (  # type: ignore
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        self._device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForSequenceClassification.from_pretrained(
            self.model_name
        ).to(self._device)
        self._model.eval()

        id2label = self._model.config.id2label
        # Build (verdict, prob_index) mapping resilient to model id-label order
        self._label_to_index: dict[NLIVerdict, int] = {}
        for idx, label in id2label.items():
            l = label.lower()
            for key, verdict in _LABEL_MAP.items():
                if key in l:
                    self._label_to_index[verdict] = int(idx)
                    break

    def verify(self, premise: str, hypothesis: str) -> NLIResult:
        if not premise or not hypothesis:
            return NLIResult(
                NLIVerdict.NEUTRAL, 0.0,
                {"entailment": 0.0, "neutral": 1.0, "contradiction": 0.0},
            )

        import torch
        with torch.no_grad():
            enc = self._tokenizer(
                premise, hypothesis,
                return_tensors="pt", truncation=True, max_length=self.max_length,
            ).to(self._device)
            logits = self._model(**enc).logits[0]
            probs = torch.softmax(logits, dim=-1).cpu().tolist()

        prob_dict = {
            "entailment": float(probs[self._label_to_index.get(NLIVerdict.ENTAILMENT, 0)]),
            "neutral": float(probs[self._label_to_index.get(NLIVerdict.NEUTRAL, 0)]),
            "contradiction": float(probs[self._label_to_index.get(NLIVerdict.CONTRADICTION, 0)]),
        }
        # Pick the highest-probability label as the verdict
        verdict_label = max(prob_dict, key=prob_dict.get)
        verdict = {
            "entailment": NLIVerdict.ENTAILMENT,
            "neutral": NLIVerdict.NEUTRAL,
            "contradiction": NLIVerdict.CONTRADICTION,
        }[verdict_label]
        return NLIResult(verdict, prob_dict[verdict_label], prob_dict)
