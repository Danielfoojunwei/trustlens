"""Real calibration module — Platt scaling, ECE, reliability diagram.

Customers run this on their labeled data to get calibrated NLI thresholds:
    "When the calibrated probability says 0.8, 80% of such claims are correct
     in my domain."

Implementation uses scikit-learn's LogisticRegression for Platt scaling
(this is the canonical reference implementation in the calibration
literature, equivalent to the Platt 1999 algorithm).

Heavy dependency (sklearn) imported lazily.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CalibrationReport:
    """Output of a calibration run."""
    n_samples: int
    ece: float                                 # Expected Calibration Error
    mce: float                                 # Maximum Calibration Error
    brier: float                               # Brier score
    reliability: list[dict] = field(default_factory=list)
    """Per-bin: {bin, count, mean_confidence, mean_accuracy}"""
    platt_a: Optional[float] = None
    platt_b: Optional[float] = None
    """Platt-scaling parameters: p_calibrated = sigmoid(a * p_raw + b)"""

    def to_dict(self) -> dict:
        return {
            "n_samples": self.n_samples,
            "ece": round(self.ece, 4),
            "mce": round(self.mce, 4),
            "brier": round(self.brier, 4),
            "reliability": self.reliability,
            "platt_a": self.platt_a,
            "platt_b": self.platt_b,
        }


def compute_ece(
    raw_confidences: list[float],
    correct: list[int],
    *,
    n_bins: int = 10,
) -> CalibrationReport:
    """Compute Expected/Maximum Calibration Error and a reliability diagram.

    Args:
        raw_confidences: list of model-reported confidences in [0, 1].
        correct: list of 0/1 labels (1 = the model was actually correct).
        n_bins: number of equal-width confidence bins.
    """
    if len(raw_confidences) != len(correct):
        raise ValueError("confidences and labels must have the same length")
    n = len(raw_confidences)
    if n == 0:
        return CalibrationReport(n_samples=0, ece=0.0, mce=0.0, brier=0.0)

    # Brier score
    brier = sum((c - y) ** 2 for c, y in zip(raw_confidences, correct)) / n

    # Bin edges 0..1 in n_bins
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for c, y in zip(raw_confidences, correct):
        idx = min(n_bins - 1, max(0, int(c * n_bins)))
        bins[idx].append((c, y))

    ece = 0.0
    mce = 0.0
    reliability: list[dict] = []
    for i, items in enumerate(bins):
        if not items:
            reliability.append({
                "bin": i, "count": 0,
                "mean_confidence": 0.0, "mean_accuracy": 0.0,
            })
            continue
        mean_conf = sum(c for c, _ in items) / len(items)
        mean_acc = sum(y for _, y in items) / len(items)
        gap = abs(mean_conf - mean_acc)
        weight = len(items) / n
        ece += weight * gap
        mce = max(mce, gap)
        reliability.append({
            "bin": i, "count": len(items),
            "mean_confidence": round(mean_conf, 4),
            "mean_accuracy": round(mean_acc, 4),
        })

    return CalibrationReport(
        n_samples=n,
        ece=ece, mce=mce, brier=brier,
        reliability=reliability,
    )


def fit_platt(
    raw_confidences: list[float],
    correct: list[int],
) -> tuple[float, float]:
    """Fit Platt scaling: p_calibrated = sigmoid(a * p_raw + b).

    Uses sklearn LogisticRegression on (raw_confidence → label).

    Returns: (a, b) parameters.
    """
    from sklearn.linear_model import LogisticRegression  # type: ignore
    import numpy as np

    X = np.asarray(raw_confidences, dtype=float).reshape(-1, 1)
    y = np.asarray(correct, dtype=int)
    if len(set(y.tolist())) < 2:
        # Degenerate — all-same labels. Identity calibration.
        return 1.0, 0.0
    lr = LogisticRegression(C=1e10, solver="lbfgs", max_iter=200)
    lr.fit(X, y)
    a = float(lr.coef_[0][0])
    b = float(lr.intercept_[0])
    return a, b


def apply_platt(p_raw: float, a: float, b: float) -> float:
    import math
    z = a * p_raw + b
    # Numerically-stable sigmoid
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def calibrate(
    raw_confidences: list[float],
    correct: list[int],
    *,
    n_bins: int = 10,
) -> CalibrationReport:
    """Full calibration: ECE/MCE + Platt parameters in one call."""
    report = compute_ece(raw_confidences, correct, n_bins=n_bins)
    a, b = fit_platt(raw_confidences, correct)
    report.platt_a = round(a, 4)
    report.platt_b = round(b, 4)
    return report
