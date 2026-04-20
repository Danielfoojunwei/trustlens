"""In-memory event log for the operator dashboard.

Keeps a bounded ring buffer of recent gateway events (requests, cert mints,
errors, health checks) and exposes:

    - ``record(event)``          push an event
    - ``recent(limit, filters)`` synchronous paginated read
    - ``stream()``               async iterator yielding new events as they
                                 arrive (used by the SSE /v1/admin/events/stream
                                 endpoint)
    - ``aggregate(window_s)``    return KPI snapshot + per-bucket time series

Everything is process-local — in a multi-replica deployment, the dashboard
reads the local replica's events only. For fleet-wide dashboards, scrape the
Prometheus ``/metrics`` surface into Grafana; see ``docs/OPERATIONS.md``.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator, Optional


@dataclass
class GatewayEvent:
    """One record in the ring buffer."""
    ts: float
    kind: str                # "request" | "cert" | "error" | "kb" | "health"
    tenant_id: Optional[str] = None
    method: Optional[str] = None
    path: Optional[str] = None
    status_code: Optional[int] = None
    model: Optional[str] = None
    latency_ms: Optional[float] = None
    cert_id: Optional[str] = None
    cert_status: Optional[str] = None
    n_claims: Optional[int] = None
    n_renderable: Optional[int] = None
    error_code: Optional[str] = None
    detail: Optional[dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != {}}


class EventLog:
    """Bounded ring buffer + fan-out to active SSE subscribers.

    Thread-safe for single-loop asyncio use. The ring buffer is bounded so
    memory stays flat under indefinite traffic. Default capacity 10,000
    events is enough for several minutes of a hot tenant.
    """

    def __init__(self, capacity: int = 10_000) -> None:
        self._buf: deque[GatewayEvent] = deque(maxlen=capacity)
        self._subscribers: list[asyncio.Queue[GatewayEvent]] = []

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------
    def record(self, event: GatewayEvent) -> None:
        self._buf.append(event)
        # fan out to active subscribers; dead queues are pruned later
        dead: list[asyncio.Queue[GatewayEvent]] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Consumer API — synchronous reads
    # ------------------------------------------------------------------
    def recent(
        self,
        limit: int = 200,
        tenant_id: Optional[str] = None,
        kind: Optional[str] = None,
        since_ts: Optional[float] = None,
    ) -> list[GatewayEvent]:
        out: list[GatewayEvent] = []
        # Iterate newest → oldest so we can cap early and respect `limit`.
        for ev in reversed(self._buf):
            if since_ts is not None and ev.ts < since_ts:
                break
            if tenant_id and ev.tenant_id != tenant_id:
                continue
            if kind and ev.kind != kind:
                continue
            out.append(ev)
            if len(out) >= limit:
                break
        out.reverse()
        return out

    def count(self) -> int:
        return len(self._buf)

    # ------------------------------------------------------------------
    # Consumer API — SSE streaming
    # ------------------------------------------------------------------
    async def stream(
        self,
        tenant_id: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> AsyncIterator[GatewayEvent]:
        """Subscribe to future events. Caller is responsible for exit."""
        q: asyncio.Queue[GatewayEvent] = asyncio.Queue(maxsize=1024)
        self._subscribers.append(q)
        try:
            while True:
                ev = await q.get()
                if tenant_id and ev.tenant_id != tenant_id:
                    continue
                if kind and ev.kind != kind:
                    continue
                yield ev
        finally:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------
    def aggregate(
        self,
        window_s: float = 300.0,
        bucket_s: float = 10.0,
        tenant_id: Optional[str] = None,
    ) -> dict:
        """KPI snapshot + per-bucket time-series over the last ``window_s``."""
        now = time.time()
        cutoff = now - window_s
        n_buckets = max(1, int(window_s // bucket_s))
        bucket_starts = [now - (n_buckets - i) * bucket_s for i in range(n_buckets)]

        req_buckets = [0] * n_buckets
        err_buckets = [0] * n_buckets
        cert_buckets = [0] * n_buckets
        latency_buckets: list[list[float]] = [[] for _ in range(n_buckets)]
        status_mix: dict[str, int] = {}
        latencies: list[float] = []

        n_requests = 0
        n_errors = 0
        n_certs = 0
        n_blocked = 0

        for ev in self._buf:
            if ev.ts < cutoff:
                continue
            if tenant_id and ev.tenant_id != tenant_id:
                continue
            idx = min(n_buckets - 1, max(0, int((ev.ts - bucket_starts[0]) // bucket_s)))
            if ev.kind == "request":
                n_requests += 1
                req_buckets[idx] += 1
                if ev.latency_ms is not None:
                    latencies.append(ev.latency_ms)
                    latency_buckets[idx].append(ev.latency_ms)
            elif ev.kind == "error":
                n_errors += 1
                err_buckets[idx] += 1
            elif ev.kind == "cert":
                n_certs += 1
                cert_buckets[idx] += 1
                if ev.cert_status:
                    status_mix[ev.cert_status] = status_mix.get(ev.cert_status, 0) + 1
                    if ev.cert_status == "blocked":
                        n_blocked += 1

        latencies_sorted = sorted(latencies)
        def _pct(p: float) -> float:
            if not latencies_sorted:
                return 0.0
            k = max(0, min(len(latencies_sorted) - 1,
                          int(round(p * (len(latencies_sorted) - 1)))))
            return latencies_sorted[k]

        per_bucket_p99 = [
            (sorted(b)[min(len(b) - 1, int(round(0.99 * (len(b) - 1))))] if b else 0.0)
            for b in latency_buckets
        ]

        return {
            "now": now,
            "window_s": window_s,
            "bucket_s": bucket_s,
            "n_requests": n_requests,
            "n_errors": n_errors,
            "n_certs": n_certs,
            "n_blocked": n_blocked,
            "error_rate": (n_errors / n_requests) if n_requests else 0.0,
            "block_rate": (n_blocked / n_certs) if n_certs else 0.0,
            "latency_ms": {
                "p50": round(_pct(0.50), 3),
                "p95": round(_pct(0.95), 3),
                "p99": round(_pct(0.99), 3),
                "mean": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
                "n":   len(latencies),
            },
            "status_mix": status_mix,
            "buckets": {
                "starts_ts":   bucket_starts,
                "requests":    req_buckets,
                "errors":      err_buckets,
                "certs":       cert_buckets,
                "p99_ms":      per_bucket_p99,
            },
        }
