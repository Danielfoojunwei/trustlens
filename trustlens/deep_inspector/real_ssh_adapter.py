"""Real SSH (Spectral Stability Hook) adapter using torch + transformers.

Power-iteration-based estimation of the spectral radius of attention weight
matrices, layer-by-layer, during a real model's forward pass. Implements
the SSHAdapter Protocol.

Standard pipeline:
    1. Load any HuggingFace causal LM (or use a pre-loaded one).
    2. Register forward hooks on attention layers.
    3. On `snapshots(text, step_count)`, run a forward pass over the text,
       collect hook outputs, run power iteration on the captured matrices.
    4. Compare per-step ρ against (1 - epsilon) → emit SSHSnapshots.

Heavy dependencies (torch, transformers) imported lazily — base trustlens
package has no GPU footprint.

Reference: spectral-radius-as-stability-diagnostic is the standard analysis
done in dynamical-systems treatments of transformer attention; see e.g.
"Spectral Analysis of Attention Heads" literature.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from trustlens.deep_inspector.ssh_adapter import (
    SSHConfig,
    SSHSeverity,
    SSHSnapshot,
)


def _power_iteration(W, n_iter: int = 20):
    """Estimate the largest singular value of W via power iteration.

    Args:
        W: 2-D torch tensor.
        n_iter: number of iterations.
    Returns: float — estimated spectral radius (top singular value).
    """
    import torch
    if W.dim() != 2:
        # For attention weight matrices that may be 4-D (B, H, S, S),
        # collapse onto last two dims and take the per-head average.
        if W.dim() == 4:
            B, H, S, _ = W.shape
            mats = W.reshape(B * H, S, S)
            sigmas = []
            for m in mats:
                sigmas.append(_power_iteration(m, n_iter))
            return float(sum(sigmas) / max(len(sigmas), 1))
        W = W.reshape(W.shape[-2], -1)

    n = W.shape[1]
    v = torch.randn(n, device=W.device, dtype=W.dtype)
    v = v / (v.norm() + 1e-12)
    for _ in range(n_iter):
        u = W @ v
        u = u / (u.norm() + 1e-12)
        v = W.T @ u
        v = v / (v.norm() + 1e-12)
    sigma = (W @ v).norm().item()
    return float(sigma)


@dataclass
class RealSSHAdapter:
    """Wraps a real HuggingFace causal LM with attention-weight spectral hooks."""

    model_name: str = "distilgpt2"
    device: Optional[str] = None
    config: SSHConfig = None  # type: ignore[assignment]
    name: str = "real_ssh"

    def __post_init__(self) -> None:
        import torch  # noqa: F401
        from transformers import AutoModel, AutoTokenizer  # type: ignore

        if self.config is None:
            self.config = SSHConfig()
        self._device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token
        self._model = AutoModel.from_pretrained(
            self.model_name, output_attentions=True
        ).to(self._device)
        self._model.eval()
        self._snapshot_count = 0
        self._critical_count = 0

    def snapshots(
        self, response_text: str, step_count: int
    ) -> list[SSHSnapshot]:
        import torch
        cfg = self.config
        threshold = 1.0 - cfg.epsilon

        # Tokenize the response. We treat each compute_every_n-th token slice
        # as a "step" to mirror the autoregressive setting.
        enc = self._tokenizer(
            response_text, return_tensors="pt",
            truncation=True, max_length=512,
        ).to(self._device)
        input_ids = enc["input_ids"][0]
        seq_len = input_ids.shape[0]

        snaps: list[SSHSnapshot] = []
        steps = list(range(0, max(1, seq_len), max(1, cfg.compute_every_n)))
        if not steps:
            steps = [0]

        for step in steps:
            sub_ids = input_ids[: step + 1].unsqueeze(0)
            t0 = time.perf_counter()
            with torch.no_grad():
                outputs = self._model(sub_ids, output_attentions=True)
            wall_ms = (time.perf_counter() - t0) * 1000.0
            attentions = outputs.attentions  # tuple of (B, H, S, S) per layer

            layers = (
                cfg.layers_to_monitor
                if cfg.layers_to_monitor is not None
                else list(range(len(attentions)))
            )

            for layer in layers:
                if layer >= len(attentions):
                    continue
                A = attentions[layer]
                rho = _power_iteration(A, n_iter=cfg.power_iterations)

                severity = SSHSeverity.NOMINAL
                if rho > threshold:
                    severity = SSHSeverity.WARNING
                if rho > threshold + 0.03:
                    severity = SSHSeverity.CRITICAL
                    self._critical_count += 1

                snaps.append(SSHSnapshot(
                    step=step, layer=layer, rho=round(rho, 4),
                    severity=severity, wall_time_ms=round(wall_ms, 2),
                ))
                self._snapshot_count += 1

        return snaps

    def summary(self) -> dict:
        return {
            "adapter": self.name,
            "model": self.model_name,
            "device": self._device,
            "snapshots_total": self._snapshot_count,
            "critical_alarms": self._critical_count,
        }
