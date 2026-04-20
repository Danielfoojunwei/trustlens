"""Per-IP token-bucket rate limit.

Purpose: defense in depth in front of the tenant-level budget tracker. A
single IP enumerating API keys should not be able to exhaust quotas across
multiple tenants; this middleware caps per-IP request rate regardless of
tenant.

This is in-process — HA deployments should front-limit at an ingress
(Envoy/NGINX/Traefik) and keep this as a backstop.
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class PerIPRateLimit(BaseHTTPMiddleware):
    """Token-bucket per client IP.

    ``rps`` is both the refill rate and the burst capacity. Requests that
    would drain the bucket below 1 are answered with 429 + Retry-After.
    """

    def __init__(self, app, rps: float = 30.0) -> None:
        super().__init__(app)
        self._rps = float(rps)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = Lock()

    def _client_ip(self, request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",", 1)[0].strip()
        return request.client.host if request.client else "unknown"

    def _consume(self, ip: str) -> Optional[float]:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(ip, (self._rps, now))
            tokens = min(self._rps, tokens + (now - last) * self._rps)
            if tokens < 1.0:
                needed = 1.0 - tokens
                retry_after = needed / self._rps if self._rps else 1.0
                self._buckets[ip] = (tokens, now)
                return retry_after
            tokens -= 1.0
            self._buckets[ip] = (tokens, now)
            return None

    async def dispatch(self, request: Request, call_next):
        ip = self._client_ip(request)
        retry_after = self._consume(ip)
        if retry_after is not None:
            import json
            return Response(
                content=json.dumps({
                    "error": {
                        "type": "rate_limited",
                        "code": "per_ip_rate_limit",
                        "message": f"too many requests from {ip}",
                        "retry_after_s": round(retry_after, 3),
                    }
                }),
                media_type="application/json",
                status_code=429,
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        return await call_next(request)
