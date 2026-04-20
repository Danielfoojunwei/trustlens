"""Data Subject Access Requests (GDPR Arts. 15-22, CCPA, India DPDP Sec. 11-13).

A ``DSARRequest`` is created when a data subject (or their representative)
exercises one of the rights below. The store tracks the per-jurisdiction
SLA deadline so the dashboard can surface "due in 3 days" type alerts.

The actual fulfilment (collecting + redacting + delivering data) happens
out-of-band; this module gives operators the queue, status, and audit trail.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional, Protocol


class DSARRequestType(str, Enum):
    ACCESS      = "access"        # GDPR Art.15  / CCPA right to know
    RECTIFY     = "rectify"       # GDPR Art.16  / CCPA right to correct
    DELETE      = "delete"        # GDPR Art.17  / CCPA right to delete
    PORTABILITY = "portability"   # GDPR Art.20
    RESTRICT    = "restrict"      # GDPR Art.18
    OBJECT      = "object"        # GDPR Art.21
    OPT_OUT     = "opt_out"       # CCPA do-not-sell/share
    LIMIT_USE   = "limit_use"     # CPRA limit use of sensitive PI


class DSARStatus(str, Enum):
    OPEN          = "open"
    IDENTITY_CHECK = "identity_check"
    IN_PROGRESS   = "in_progress"
    FULFILLED     = "fulfilled"
    REJECTED      = "rejected"     # with documented lawful reason
    OVERDUE       = "overdue"


# Per-jurisdiction default SLA in days. The profile system can override.
SLA_DAYS = {
    "gdpr":       30,
    "ccpa":       45,
    "india_dpdp": 30,
    "korea_ai":   30,
    "default":    30,
}


@dataclass
class DSARRequest:
    request_id: str
    tenant_id: str
    data_subject_id: str
    type: str
    jurisdiction: str            # "gdpr" / "ccpa" / "india_dpdp" / "default"
    submitted_at: float
    deadline_at: float
    status: str
    received_via: Optional[str] = None     # "email" | "ui" | "api"
    contact: Optional[str] = None          # how to reply (encrypted)
    notes: list[str] = field(default_factory=list)
    fulfilled_at: Optional[float] = None
    fulfilled_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    artifact_uri: Optional[str] = None     # where the deliverable lives

    def to_dict(self) -> dict:
        return asdict(self)

    def days_to_deadline(self, now: Optional[float] = None) -> float:
        return (self.deadline_at - (now or time.time())) / 86_400.0

    def is_overdue(self, now: Optional[float] = None) -> bool:
        if self.status in (DSARStatus.FULFILLED.value, DSARStatus.REJECTED.value):
            return False
        return (now or time.time()) > self.deadline_at


class DSARStore(Protocol):
    def open(self, *, tenant_id: str, data_subject_id: str, type: str,
             jurisdiction: str = "gdpr",
             received_via: Optional[str] = None,
             contact: Optional[str] = None) -> DSARRequest: ...
    def get(self, request_id: str) -> Optional[DSARRequest]: ...
    def update(self, request_id: str, *,
               status: Optional[str] = None,
               note: Optional[str] = None,
               fulfilled_by: Optional[str] = None,
               rejection_reason: Optional[str] = None,
               artifact_uri: Optional[str] = None) -> Optional[DSARRequest]: ...
    def all(self, *,
            tenant_id: Optional[str] = None,
            status: Optional[str] = None,
            include_overdue_recompute: bool = True) -> list[DSARRequest]: ...
    def overdue(self, tenant_id: Optional[str] = None) -> list[DSARRequest]: ...


class InMemoryDSARStore:
    def __init__(self) -> None:
        self._items: dict[str, DSARRequest] = {}

    def open(self, *, tenant_id: str, data_subject_id: str, type: str,
             jurisdiction: str = "gdpr",
             received_via: Optional[str] = None,
             contact: Optional[str] = None) -> DSARRequest:
        sla = SLA_DAYS.get(jurisdiction, SLA_DAYS["default"])
        now = time.time()
        rid = "dsar_" + secrets.token_hex(6)
        r = DSARRequest(
            request_id=rid, tenant_id=tenant_id,
            data_subject_id=data_subject_id, type=type,
            jurisdiction=jurisdiction, submitted_at=now,
            deadline_at=now + sla * 86_400.0,
            status=DSARStatus.OPEN.value,
            received_via=received_via, contact=contact,
        )
        self._items[rid] = r
        return r

    def get(self, request_id: str) -> Optional[DSARRequest]:
        return self._items.get(request_id)

    def update(self, request_id: str, *,
               status: Optional[str] = None,
               note: Optional[str] = None,
               fulfilled_by: Optional[str] = None,
               rejection_reason: Optional[str] = None,
               artifact_uri: Optional[str] = None) -> Optional[DSARRequest]:
        r = self._items.get(request_id)
        if r is None:
            return None
        if status is not None:        r.status = status
        if note:                      r.notes.append(note)
        if fulfilled_by is not None:  r.fulfilled_by = fulfilled_by
        if rejection_reason is not None: r.rejection_reason = rejection_reason
        if artifact_uri is not None:  r.artifact_uri = artifact_uri
        if status == DSARStatus.FULFILLED.value and r.fulfilled_at is None:
            r.fulfilled_at = time.time()
        return r

    def all(self, *,
            tenant_id: Optional[str] = None,
            status: Optional[str] = None,
            include_overdue_recompute: bool = True) -> list[DSARRequest]:
        out = list(self._items.values())
        if tenant_id is not None:
            out = [r for r in out if r.tenant_id == tenant_id]
        if include_overdue_recompute:
            for r in out:
                if r.is_overdue() and r.status not in (
                    DSARStatus.FULFILLED.value, DSARStatus.REJECTED.value,
                    DSARStatus.OVERDUE.value,
                ):
                    r.status = DSARStatus.OVERDUE.value
        if status is not None:
            out = [r for r in out if r.status == status]
        out.sort(key=lambda r: r.submitted_at, reverse=True)
        return out

    def overdue(self, tenant_id: Optional[str] = None) -> list[DSARRequest]:
        return [r for r in self.all(tenant_id=tenant_id) if r.is_overdue()]
