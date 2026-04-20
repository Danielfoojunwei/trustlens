"""Shadow evaluation.

A small, randomized fraction of production traffic is mirrored into an
offline evaluation queue for continuous drift detection:
    - Is SSH precision/recall holding on live data?
    - Are oracles returning stale answers?
    - Is the capability/safety Pareto shifting?

The sampler is deterministic per (tenant_id, request_id) so a sample can be
replayed and the same decision reproduced.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from trustlens.certificate.schema import Certificate


@dataclass
class ShadowSample:
    cert_id: str
    tenant_id: str
    sampled_at: float
    renderable_text: str
    payload: dict = field(default_factory=dict)


class ShadowEvalSampler:
    """Deterministic sampler + durable queue.

    In production, replace the local queue/file writer with a Kafka/NATS
    producer and a real labeling service.
    """

    def __init__(
        self,
        sample_rate: float = 0.01,
        queue_path: Optional[str] = None,
        max_queue_size: int = 10_000,
    ):
        self.sample_rate = max(0.0, min(1.0, sample_rate))
        self._queue_path = (
            Path(queue_path) if queue_path
            else Path(os.environ.get("TRUSTLENS_SHADOW_QUEUE", "./.trustlens/shadow"))
        )
        self._queue_path.mkdir(parents=True, exist_ok=True)
        self._inproc: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._drain_lock = threading.Lock()

    def should_sample(self, tenant_id: str, request_id: str) -> bool:
        if self.sample_rate <= 0:
            return False
        if self.sample_rate >= 1.0:
            return True
        # Deterministic hash-based sampling — same req_id always same decision
        key = f"{tenant_id}:{request_id}".encode("utf-8")
        h = int(hashlib.sha256(key).hexdigest()[:8], 16) / 0xFFFFFFFF
        return h < self.sample_rate

    def submit(self, cert: Certificate, renderable_text: str) -> None:
        sample = ShadowSample(
            cert_id=cert.cert_id,
            tenant_id=cert.payload.tenant_id,
            sampled_at=time.time(),
            renderable_text=renderable_text,
            payload=cert.payload.model_dump(mode="json"),
        )
        try:
            self._inproc.put_nowait(sample)
        except queue.Full:
            # Shed silently — shadow eval must NEVER block the hot path
            return
        self._drain_if_due()

    def pending(self) -> int:
        return self._inproc.qsize()

    def drain(self) -> int:
        """Flush all queued samples to disk as a JSONL log. Returns count."""
        with self._drain_lock:
            flushed = 0
            fname = self._queue_path / f"shadow-{int(time.time())}.jsonl"
            items: list[ShadowSample] = []
            while True:
                try:
                    items.append(self._inproc.get_nowait())
                except queue.Empty:
                    break
            if not items:
                return 0
            with fname.open("a", encoding="utf-8") as f:
                for s in items:
                    f.write(json.dumps(asdict(s), default=str) + "\n")
                    flushed += 1
            return flushed

    def _drain_if_due(self) -> None:
        # Cheap: drain when we hit 256 pending items. Production should use
        # a background thread/task instead.
        if self._inproc.qsize() >= 256:
            self.drain()
