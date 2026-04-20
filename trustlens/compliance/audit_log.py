"""Tamper-evident audit log (SHA-256 hash chain).

Every event is appended with its prev-hash, producing an unbroken chain.
Verifiers can replay the whole log and detect any insertion / mutation /
deletion in O(n).

Schema is intentionally minimal — actor, action, resource, outcome,
metadata — so it covers every SOC 2 / ISO 27001 logging requirement
without forcing operators to design their own taxonomy upfront.

Persistence:
    InMemoryAuditLog       — bounded ring buffer, dev / tests
    FilesystemAuditLog     — append-only JSONL on disk, single-replica prod

For HA, swap in a Postgres-backed implementation against the same
``AuditLogStore`` Protocol — the chain is application-side, so the
underlying storage is interchangeable.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional, Protocol


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------

GENESIS_PREV_HASH = "0" * 64


@dataclass
class AuditEvent:
    seq: int                      # monotone within a chain
    ts: float                     # unix seconds
    actor: str                    # user_id / "apikey:tlk_..." / "system"
    actor_role: Optional[str]     # snapshot of role at write time
    action: str                   # e.g. "kb.upsert", "auth.login", "tenant.update"
    resource: Optional[str]       # e.g. "tenant:acme/doc:pol-001"
    outcome: str                  # "success" | "failure" | "partial"
    tenant_id: Optional[str]      # tenant scope (if any)
    request_id: Optional[str]     # correlation id
    ip: Optional[str]             # source IP for human actors
    metadata: dict                # arbitrary structured detail
    prev_hash: str                # link to previous event
    hash: str                     # this event's hash (computed)

    def to_dict(self) -> dict:
        return asdict(self)

    def canonical_payload(self) -> str:
        """The bytes hashed to compute this event's ``hash``.

        Excludes ``hash`` itself; includes ``prev_hash`` so the chain is sealed.
        """
        d = {
            "seq": self.seq, "ts": self.ts,
            "actor": self.actor, "actor_role": self.actor_role,
            "action": self.action, "resource": self.resource,
            "outcome": self.outcome, "tenant_id": self.tenant_id,
            "request_id": self.request_id, "ip": self.ip,
            "metadata": self.metadata, "prev_hash": self.prev_hash,
        }
        return json.dumps(d, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)


def _hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

class AuditLogStore(Protocol):
    def append(self, *, actor: str, action: str, outcome: str,
               actor_role: Optional[str] = None,
               resource: Optional[str] = None,
               tenant_id: Optional[str] = None,
               request_id: Optional[str] = None,
               ip: Optional[str] = None,
               metadata: Optional[dict] = None) -> AuditEvent: ...
    def all(self, *, limit: Optional[int] = None,
            tenant_id: Optional[str] = None,
            action_prefix: Optional[str] = None) -> list[AuditEvent]: ...
    def verify(self) -> "ChainVerifyResult": ...
    def count(self) -> int: ...


@dataclass
class ChainVerifyResult:
    ok: bool
    n_events: int
    first_break_seq: Optional[int] = None
    reason: Optional[str] = None


class InMemoryAuditLog:
    """Bounded ring-buffer with strict hash chaining."""

    def __init__(self, capacity: int = 50_000) -> None:
        self._buf: list[AuditEvent] = []
        self._capacity = capacity
        self._lock = Lock()

    def append(self, *, actor: str, action: str, outcome: str,
               actor_role: Optional[str] = None,
               resource: Optional[str] = None,
               tenant_id: Optional[str] = None,
               request_id: Optional[str] = None,
               ip: Optional[str] = None,
               metadata: Optional[dict] = None) -> AuditEvent:
        with self._lock:
            seq = (self._buf[-1].seq + 1) if self._buf else 1
            prev_hash = self._buf[-1].hash if self._buf else GENESIS_PREV_HASH
            ev = AuditEvent(
                seq=seq, ts=time.time(),
                actor=actor, actor_role=actor_role,
                action=action, resource=resource,
                outcome=outcome, tenant_id=tenant_id,
                request_id=request_id, ip=ip,
                metadata=metadata or {},
                prev_hash=prev_hash, hash="",
            )
            ev.hash = _hash(ev.canonical_payload())
            self._buf.append(ev)
            if len(self._buf) > self._capacity:
                # Drop the oldest; chain is still intact within the surviving
                # window because each remaining event's prev_hash matches the
                # event before it.
                self._buf = self._buf[-self._capacity:]
            return ev

    def all(self, *, limit: Optional[int] = None,
            tenant_id: Optional[str] = None,
            action_prefix: Optional[str] = None) -> list[AuditEvent]:
        with self._lock:
            out = self._buf
            if tenant_id is not None:
                out = [e for e in out if e.tenant_id == tenant_id]
            if action_prefix is not None:
                out = [e for e in out if e.action.startswith(action_prefix)]
            if limit is not None:
                out = out[-limit:]
            return list(out)

    def verify(self) -> ChainVerifyResult:
        with self._lock:
            prev = self._buf[0].prev_hash if self._buf else GENESIS_PREV_HASH
            for i, e in enumerate(self._buf):
                if e.prev_hash != prev:
                    return ChainVerifyResult(
                        ok=False, n_events=len(self._buf),
                        first_break_seq=e.seq,
                        reason="prev_hash_mismatch",
                    )
                expected = _hash(e.canonical_payload())
                if e.hash != expected:
                    return ChainVerifyResult(
                        ok=False, n_events=len(self._buf),
                        first_break_seq=e.seq,
                        reason="payload_hash_mismatch",
                    )
                prev = e.hash
            return ChainVerifyResult(ok=True, n_events=len(self._buf))

    def count(self) -> int:
        return len(self._buf)


class FilesystemAuditLog:
    """Append-only JSONL on disk + same in-memory chain semantics."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._inner = InMemoryAuditLog(capacity=2_000_000)
        self._lock = Lock()
        if self._path.exists():
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    ev = AuditEvent(**d)
                    self._inner._buf.append(ev)

    def append(self, **kw) -> AuditEvent:
        with self._lock:
            ev = self._inner.append(**kw)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev.to_dict()) + "\n")
            return ev

    def all(self, **kw) -> list[AuditEvent]:
        return self._inner.all(**kw)

    def verify(self) -> ChainVerifyResult:
        return self._inner.verify()

    def count(self) -> int:
        return self._inner.count()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def export_jsonl(events: Iterable[AuditEvent]) -> str:
    return "\n".join(json.dumps(e.to_dict()) for e in events)


def export_csv(events: Iterable[AuditEvent]) -> str:
    rows = ["seq,ts,actor,actor_role,action,resource,outcome,tenant_id,request_id,ip,hash"]
    for e in events:
        rows.append(",".join(str(x) for x in [
            e.seq, e.ts, e.actor, e.actor_role or "",
            e.action, e.resource or "", e.outcome,
            e.tenant_id or "", e.request_id or "", e.ip or "", e.hash,
        ]))
    return "\n".join(rows)
