"""Consent records (GDPR Art.7, ISO 27701 A.7.2.4, CCPA opt-out, India DPDP Sec.6).

A ``ConsentRecord`` is per (tenant, data_subject_id, purpose). The store
keeps the full history so an auditor can reconstruct the consent state at
any point in time. Withdrawals are appended, not overwriting.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional, Protocol


class ConsentStatus(str, Enum):
    GRANTED   = "granted"
    WITHDRAWN = "withdrawn"
    EXPIRED   = "expired"


class ConsentPurpose(str, Enum):
    """Common processing purposes — extend per deployment."""
    SERVICE_DELIVERY  = "service_delivery"     # core functionality
    AI_TRAINING       = "ai_training"           # use prompts/responses for training
    PERSONALIZATION   = "personalization"       # behavioral profiling
    ANALYTICS         = "analytics"             # aggregated usage analytics
    MARKETING         = "marketing"             # marketing communications
    THIRD_PARTY_SHARE = "third_party_share"     # CCPA "sale/sharing"
    SENSITIVE_PI      = "sensitive_pi"          # CPRA sensitive PI use


@dataclass
class ConsentRecord:
    record_id: str
    tenant_id: str
    data_subject_id: str          # opaque hash of email or external user id
    purpose: str                  # ConsentPurpose value
    status: str                   # ConsentStatus value
    ts: float
    lawful_basis: Optional[str] = None     # "consent" | "contract" | "legal_obligation" | ...
    expires_at: Optional[float] = None
    captured_via: Optional[str] = None     # "ui" | "api" | "import"
    evidence_uri: Optional[str] = None     # where the proof lives (S3 path, ticket id, ...)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class ConsentStore(Protocol):
    def record(self, *, tenant_id: str, data_subject_id: str, purpose: str,
               status: str, lawful_basis: Optional[str] = None,
               expires_at: Optional[float] = None,
               captured_via: Optional[str] = None,
               evidence_uri: Optional[str] = None,
               metadata: Optional[dict] = None) -> ConsentRecord: ...
    def history(self, tenant_id: str, data_subject_id: str) -> list[ConsentRecord]: ...
    def current(self, tenant_id: str, data_subject_id: str,
                purpose: str) -> Optional[ConsentRecord]: ...
    def all_for_tenant(self, tenant_id: str,
                       limit: Optional[int] = None) -> list[ConsentRecord]: ...


class InMemoryConsentStore:
    def __init__(self) -> None:
        self._records: list[ConsentRecord] = []

    def record(self, *, tenant_id: str, data_subject_id: str, purpose: str,
               status: str, lawful_basis: Optional[str] = None,
               expires_at: Optional[float] = None,
               captured_via: Optional[str] = None,
               evidence_uri: Optional[str] = None,
               metadata: Optional[dict] = None) -> ConsentRecord:
        r = ConsentRecord(
            record_id="cons_" + secrets.token_hex(6),
            tenant_id=tenant_id, data_subject_id=data_subject_id,
            purpose=purpose, status=status, ts=time.time(),
            lawful_basis=lawful_basis, expires_at=expires_at,
            captured_via=captured_via, evidence_uri=evidence_uri,
            metadata=metadata or {},
        )
        self._records.append(r)
        return r

    def history(self, tenant_id: str, data_subject_id: str) -> list[ConsentRecord]:
        return [r for r in self._records
                if r.tenant_id == tenant_id and r.data_subject_id == data_subject_id]

    def current(self, tenant_id: str, data_subject_id: str,
                purpose: str) -> Optional[ConsentRecord]:
        rs = [r for r in self.history(tenant_id, data_subject_id)
              if r.purpose == purpose]
        if not rs:
            return None
        latest = max(rs, key=lambda r: r.ts)
        if latest.expires_at and latest.expires_at < time.time():
            # Synthesise an EXPIRED row so callers can react without writing.
            return ConsentRecord(
                record_id=latest.record_id + ":expired",
                tenant_id=latest.tenant_id, data_subject_id=latest.data_subject_id,
                purpose=latest.purpose, status=ConsentStatus.EXPIRED.value,
                ts=latest.expires_at, lawful_basis=latest.lawful_basis,
            )
        return latest

    def all_for_tenant(self, tenant_id: str,
                       limit: Optional[int] = None) -> list[ConsentRecord]:
        rs = [r for r in self._records if r.tenant_id == tenant_id]
        rs.sort(key=lambda r: r.ts, reverse=True)
        return rs if limit is None else rs[:limit]
