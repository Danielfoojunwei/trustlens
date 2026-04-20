"""Incident recorder + webhook alerter.

Receives structured events from the verifier pipeline (SSH critical alarm,
RAD-CoT steering engagement, oracle outage, budget exhaustion, cert
block rate spike) and:

    1. Stores them in a bounded ring buffer (similar to ``event_log``).
    2. Fans out to subscribed SSE clients.
    3. If an ``alerts.webhook`` or ``alerts.slack`` integration is enabled,
       POSTs the incident body to the configured URL.

All writes are sync-safe single-loop asyncio. Nothing is persisted to
disk; swap ``InMemoryIncidentStore`` for a DB/Kafka-backed store in HA.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator, Optional

from trustlens.integrations import IntegrationsStore


class Severity:
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


INCIDENT_KINDS = {
    "ssh.critical",       # attention matrix entered unstable regime
    "ssh.warning",
    "radcot.engage",      # activation steering fired
    "radcot.disengage",
    "oracle.outage",
    "oracle.slow",
    "budget.exhausted",
    "block_rate.spike",
    "backend.down",
    "custom",
}


@dataclass
class Incident:
    incident_id: str
    ts: float
    kind: str
    severity: str
    tenant_id: Optional[str] = None
    title: str = ""
    detail: dict = field(default_factory=dict)
    cert_id: Optional[str] = None
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, {}, "")}


class IncidentRecorder:
    """Ring buffer + SSE fan-out + optional webhook alerting."""

    def __init__(
        self,
        capacity: int = 2000,
        integrations: Optional[IntegrationsStore] = None,
    ) -> None:
        self._buf: list[Incident] = []
        self._capacity = capacity
        self._subscribers: list[asyncio.Queue[Incident]] = []
        self._integrations = integrations

    # ------------------------------------------------------------------
    def record(
        self,
        kind: str,
        severity: str,
        title: str,
        *,
        tenant_id: Optional[str] = None,
        cert_id: Optional[str] = None,
        detail: Optional[dict] = None,
    ) -> Incident:
        inc = Incident(
            incident_id="inc_" + uuid.uuid4().hex[:10],
            ts=time.time(), kind=kind, severity=severity,
            tenant_id=tenant_id, title=title, detail=detail or {},
            cert_id=cert_id,
        )
        self._buf.append(inc)
        if len(self._buf) > self._capacity:
            self._buf = self._buf[-self._capacity:]
        for q in list(self._subscribers):
            try:
                q.put_nowait(inc)
            except asyncio.QueueFull:
                try: self._subscribers.remove(q)
                except ValueError: pass
        if self._integrations is not None:
            asyncio.create_task(self._fanout_webhooks(inc))
        return inc

    def recent(
        self, limit: int = 200,
        severity: Optional[str] = None,
        kind: Optional[str] = None,
        tenant_id: Optional[str] = None,
        acked: Optional[bool] = None,
    ) -> list[Incident]:
        out: list[Incident] = []
        for inc in reversed(self._buf):
            if severity and inc.severity != severity: continue
            if kind and inc.kind != kind: continue
            if tenant_id and inc.tenant_id != tenant_id: continue
            if acked is True and inc.acknowledged_at is None: continue
            if acked is False and inc.acknowledged_at is not None: continue
            out.append(inc)
            if len(out) >= limit: break
        out.reverse()
        return out

    def acknowledge(self, incident_id: str, user_id: str) -> Optional[Incident]:
        for inc in self._buf:
            if inc.incident_id == incident_id:
                inc.acknowledged_at = time.time()
                inc.acknowledged_by = user_id
                return inc
        return None

    async def stream(self) -> AsyncIterator[Incident]:
        q: asyncio.Queue[Incident] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            try: self._subscribers.remove(q)
            except ValueError: pass

    def count(self) -> int:
        return len(self._buf)

    # ------------------------------------------------------------------
    async def _fanout_webhooks(self, inc: Incident) -> None:
        if self._integrations is None:
            return
        try:
            import httpx  # type: ignore
        except Exception:
            return
        for kind in ("alerts.webhook", "alerts.slack"):
            integ = self._integrations.get(kind)
            if not integ or not integ.enabled:
                continue
            url = integ.config.get("url")
            if not url:
                continue
            body: dict = inc.to_dict()
            if kind == "alerts.slack":
                body = {"text": f"*{inc.severity.upper()}* · {inc.kind} · {inc.title}",
                        "attachments": [{"text": json.dumps(inc.detail)}]}
            try:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    await c.post(url, json=body)
            except Exception:
                pass
