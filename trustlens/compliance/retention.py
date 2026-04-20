"""Data retention policies (GDPR Art.5(e) storage limitation,
ISO 27001 A.8.10 information deletion, ISO 27701 A.7.4.5,
CCPA 1798.105 deletion).

Operators declare ``RetentionPolicy`` per data class per tenant.
``compute_due()`` returns the items past their retention horizon so a
purge job (out of scope here — wire your own task) can act on them.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional, Protocol


class DataClass(str, Enum):
    """Coarse data-class taxonomy. Extend per deployment."""
    CERTIFICATES   = "certificates"        # signed cert objects
    AUDIT_LOG      = "audit_log"           # the chain itself
    CHAT_LOGS      = "chat_logs"           # request/response bodies
    KB_DOCUMENTS   = "kb_documents"        # tenant KB
    INCIDENTS      = "incidents"           # incident history
    BENCH_EVENTS   = "bench_events"        # benchmark / shadow-eval
    USER_PROFILES  = "user_profiles"       # users, sessions, api keys


@dataclass
class RetentionPolicy:
    tenant_id: str
    data_class: str
    retention_days: int
    deletion_method: str = "purge"          # "purge" | "anonymize" | "archive"
    legal_hold: bool = False                # if true, never auto-delete
    notes: str = ""
    updated_at: float = field(default_factory=time.time)
    updated_by: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class RetentionStore(Protocol):
    def set(self, policy: RetentionPolicy) -> None: ...
    def get(self, tenant_id: str, data_class: str) -> Optional[RetentionPolicy]: ...
    def all(self, tenant_id: Optional[str] = None) -> list[RetentionPolicy]: ...
    def delete(self, tenant_id: str, data_class: str) -> bool: ...


class InMemoryRetentionStore:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], RetentionPolicy] = {}

    def set(self, policy: RetentionPolicy) -> None:
        self._by_key[(policy.tenant_id, policy.data_class)] = policy

    def get(self, tenant_id: str, data_class: str) -> Optional[RetentionPolicy]:
        return self._by_key.get((tenant_id, data_class))

    def all(self, tenant_id: Optional[str] = None) -> list[RetentionPolicy]:
        out = list(self._by_key.values())
        if tenant_id:
            out = [p for p in out if p.tenant_id == tenant_id]
        out.sort(key=lambda p: (p.tenant_id, p.data_class))
        return out

    def delete(self, tenant_id: str, data_class: str) -> bool:
        return self._by_key.pop((tenant_id, data_class), None) is not None


def default_policies(tenant_id: str) -> list[RetentionPolicy]:
    """Reasonable defaults for a fresh tenant — operators MUST review."""
    return [
        RetentionPolicy(tenant_id=tenant_id, data_class=DataClass.CERTIFICATES.value,
                        retention_days=2_555,  # 7 years (financial / EU AI Act Art.18 bar)
                        notes="EU AI Act Art.12 + 18 require 6 months min for high-risk; default to 7y for safety."),
        RetentionPolicy(tenant_id=tenant_id, data_class=DataClass.AUDIT_LOG.value,
                        retention_days=2_555,
                        notes="SOC 2 / ISO 27001 — typically 7y; legal hold may extend."),
        RetentionPolicy(tenant_id=tenant_id, data_class=DataClass.CHAT_LOGS.value,
                        retention_days=180,
                        deletion_method="anonymize",
                        notes="GDPR storage limitation — short by default."),
        RetentionPolicy(tenant_id=tenant_id, data_class=DataClass.KB_DOCUMENTS.value,
                        retention_days=365 * 3,
                        notes="Customer-owned content — typically long retention with explicit purge."),
        RetentionPolicy(tenant_id=tenant_id, data_class=DataClass.INCIDENTS.value,
                        retention_days=365 * 2,
                        notes="DORA Art.19 + ISO A.5.27 — keep for incident-trend analysis."),
        RetentionPolicy(tenant_id=tenant_id, data_class=DataClass.BENCH_EVENTS.value,
                        retention_days=365),
        RetentionPolicy(tenant_id=tenant_id, data_class=DataClass.USER_PROFILES.value,
                        retention_days=365,
                        deletion_method="anonymize"),
    ]


def compute_due(policy: RetentionPolicy,
                items: list[dict],
                ts_field: str = "ts",
                now: Optional[float] = None) -> list[dict]:
    """Return items whose age exceeds the policy and are not under legal hold."""
    if policy.legal_hold:
        return []
    cutoff = (now or time.time()) - policy.retention_days * 86_400.0
    return [i for i in items if i.get(ts_field, time.time()) < cutoff]
