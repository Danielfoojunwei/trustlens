"""Real RAD-CoT steering adapter using torch forward hooks.

Loads steering vectors per layer (computed from a contrastive pair set or
loaded from disk) and adds `alpha * v_layer` to the residual stream of the
selected transformer layers when engaged.

Implements the SteeringAdapter Protocol so the DeepVerifierEngine can swap
this in for the StubSteeringAdapter without code changes elsewhere.

Heavy dependencies (torch, transformers) imported lazily.

Reference: this is the standard activation-engineering approach surveyed in
e.g. "Steering Llama 2 via Contrastive Activation Addition" and the
representation-engineering literature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from trustlens.deep_inspector.steering_adapter import (
    SteeringConfig,
    SteeringEvent,
)


@dataclass
class RealSteeringAdapter:
    """Real activation-steering hook attached to a HuggingFace causal LM.

    Args:
        model: A pre-loaded HuggingFace model (any causal LM with `.model.layers`
               or `.transformer.h`).
        steering_vectors: dict[int, torch.Tensor] — layer → steering direction
                          (one vector per monitored layer, shape (d_model,)).
        config: SteeringConfig.
    """

    model: object = None  # type: ignore[assignment]
    steering_vectors: dict = field(default_factory=dict)
    config: SteeringConfig = None  # type: ignore[assignment]
    name: str = "real_steering"

    def __post_init__(self) -> None:
        import torch  # noqa: F401
        if self.config is None:
            self.config = SteeringConfig()
        self._handles: list = []
        self._engaged = False
        self._events: list[SteeringEvent] = []
        self._engagements = 0
        self._disengagements = 0
        self._current_scale: float = 0.0
        self._active_layers = sorted(self.steering_vectors.keys())[: self.config.top_k_layers]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def engage(self, scale: float, rho: Optional[float] = None, step: int = 0) -> None:
        if self._engaged:
            return
        effective = min(scale * self.config.alpha, self.config.max_alpha)
        self._current_scale = effective
        self._attach_hooks(effective)
        self._events.append(SteeringEvent(
            kind="engage", at_step=step,
            scale=round(effective, 4), rho=rho,
            layer_count=len(self._active_layers),
        ))
        self._engaged = True
        self._engagements += 1

    def disengage(self, step: int = 0) -> None:
        if not self._engaged:
            return
        self._remove_hooks()
        self._events.append(SteeringEvent(
            kind="disengage", at_step=step, scale=0.0,
            layer_count=len(self._active_layers),
        ))
        self._engaged = False
        self._current_scale = 0.0
        self._disengagements += 1

    def adaptive_scale(self, rho: float, epsilon: float) -> float:
        threshold = 1.0 - epsilon
        if rho <= threshold:
            return 0.0
        excess = rho - threshold
        return float(min(excess / 0.5, 1.0))

    def events(self) -> list[SteeringEvent]:
        return list(self._events)

    def reset(self) -> None:
        self._remove_hooks()
        self._engaged = False
        self._current_scale = 0.0
        self._events.clear()

    def summary(self) -> dict:
        return {
            "adapter": self.name,
            "engagements": self._engagements,
            "disengagements": self._disengagements,
            "currently_engaged": self._engaged,
            "current_scale": round(self._current_scale, 3),
            "active_layers": self._active_layers,
            "alpha": self.config.alpha,
            "max_alpha": self.config.max_alpha,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load_vectors(cls, path: str | Path) -> dict:
        """Load steering vectors from a torch .pt file mapping int → Tensor."""
        import torch
        return torch.load(path, map_location="cpu")

    @classmethod
    def save_vectors(cls, vectors: dict, path: str | Path) -> None:
        import torch
        torch.save(vectors, path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_layers(self) -> list:
        m = self.model
        for path_fn in (
            lambda x: x.model.layers,
            lambda x: x.transformer.h,
            lambda x: x.model.decoder.layers,
        ):
            try:
                ls = path_fn(m)
                if ls is not None and len(ls) > 0:
                    return list(ls)
            except AttributeError:
                continue
        return []

    def _attach_hooks(self, scale: float) -> None:
        layers = self._get_layers()
        if not layers:
            return
        import torch
        for li in self._active_layers:
            if li >= len(layers):
                continue
            v = self.steering_vectors.get(li)
            if v is None:
                continue
            target = layers[li]
            v_dev = v

            def make_hook(steering_v, s):
                def hook_fn(module, _inputs, output):
                    if isinstance(output, tuple):
                        hs = output[0]
                        v2 = steering_v.to(hs.device, hs.dtype) * s
                        return (hs + v2.unsqueeze(0).unsqueeze(0),) + output[1:]
                    v2 = steering_v.to(output.device, output.dtype) * s
                    return output + v2.unsqueeze(0).unsqueeze(0)
                return hook_fn

            handle = target.register_forward_hook(make_hook(v_dev, scale))
            self._handles.append(handle)

    def _remove_hooks(self) -> None:
        for h in self._handles:
            h.remove()
        self._handles.clear()
