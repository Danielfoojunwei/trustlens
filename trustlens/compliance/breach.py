"""Breach + serious-incident reporting.

Maps a single internal breach event onto the per-jurisdiction reporting
window:

    GDPR Art.33     — 72h to supervisory authority
    GDPR Art.34     — without undue delay to data subjects (if high risk)
    CCPA 1798.150   — 45 days to consumers
    DORA Art.19     — 4h initial / 72h intermediate / 30d final
    EU AI Act Art.73 — 15 days for serious incidents
    HIPAA           — 60 days
    India DPDP      — 72h to DPB
    Korea AI        — without undue delay
    SEC Cyber       — 4 business days

This is the structured record + classification + countdown. Actual
notification (email / phone / postal mail) is out of scope.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional, Protocol


class BreachSeverity(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


class BreachKind(str, Enum):
    CONFIDENTIALITY = "confidentiality"   # unauthorized disclosure
    INTEGRITY       = "integrity"          # unauthorized modification
    AVAILABILITY    = "availability"       # outage / DoS
    AI_HARM         = "ai_harm"            # serious incident from AI output
    INSIDER         = "insider"            # malicious insider
    SUPPLY_CHAIN    = "supply_chain"       # vendor compromise


REPORTING_WINDOWS_HOURS = {
    "gdpr_dpa":      72,
    "gdpr_subjects": 72 * 3,    # "without undue delay" — we surface 9d as a soft cap
    "ccpa":          45 * 24,
    "dora_initial":  4,
    "dora_interim":  72,
    "dora_final":    30 * 24,
    "eu_ai_act":     15 * 24,
    "hipaa":         60 * 24,
    "india_dpdp":    72,
    "korea_ai":      72,        # "undue delay" — track 72h as soft cap
    "sec_cyber":     4 * 24,    # "4 business days" — use 96h for monotonic countdown
}


@dataclass
class BreachReport:
    breach_id: str
    tenant_id: Optional[str]
    detected_at: float
    severity: str
    kind: str
    title: str
    summary: str
    affected_subjects_estimate: Optional[int] = None
    data_classes: list[str] = field(default_factory=list)
    jurisdictions: list[str] = field(default_factory=list)
    notifications_due: dict = field(default_factory=dict)   # window_id -> deadline_ts
    notifications_sent: dict = field(default_factory=dict)  # window_id -> sent_at
    rcca_uri: Optional[str] = None                          # root cause / corrective action
    closed_at: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _windows_for(jurisdictions: list[str]) -> list[str]:
    """Map jurisdiction tags to applicable reporting-window IDs."""
    out: set[str] = set()
    for j in jurisdictions:
        j = j.lower()
        if j in {"eu", "gdpr"}:
            out.update({"gdpr_dpa", "gdpr_subjects"})
        if j == "ccpa":
            out.add("ccpa")
        if j in {"dora", "eu_finance"}:
            out.update({"dora_initial", "dora_interim", "dora_final"})
        if j in {"eu_ai_act", "eu_ai"}:
            out.add("eu_ai_act")
        if j == "hipaa":
            out.add("hipaa")
        if j in {"india_dpdp", "india"}:
            out.add("india_dpdp")
        if j in {"korea_ai", "korea"}:
            out.add("korea_ai")
        if j in {"sec_cyber", "sec"}:
            out.add("sec_cyber")
    return sorted(out)


class BreachStore(Protocol):
    def open(self, *, tenant_id: Optional[str], severity: str, kind: str,
             title: str, summary: str, jurisdictions: list[str],
             data_classes: list[str],
             affected_subjects_estimate: Optional[int] = None,
             metadata: Optional[dict] = None) -> BreachReport: ...
    def get(self, breach_id: str) -> Optional[BreachReport]: ...
    def mark_notified(self, breach_id: str, window_id: str) -> Optional[BreachReport]: ...
    def close(self, breach_id: str, rcca_uri: Optional[str] = None) -> Optional[BreachReport]: ...
    def all(self, tenant_id: Optional[str] = None) -> list[BreachReport]: ...
    def overdue(self) -> list[tuple[BreachReport, str, float]]: ...


class InMemoryBreachStore:
    def __init__(self) -> None:
        self._items: dict[str, BreachReport] = {}

    def open(self, *, tenant_id: Optional[str], severity: str, kind: str,
             title: str, summary: str, jurisdictions: list[str],
             data_classes: list[str],
             affected_subjects_estimate: Optional[int] = None,
             metadata: Optional[dict] = None) -> BreachReport:
        bid = "br_" + secrets.token_hex(6)
        now = time.time()
        windows = _windows_for(jurisdictions)
        due = {w: now + REPORTING_WINDOWS_HOURS[w] * 3600.0 for w in windows}
        r = BreachReport(
            breach_id=bid, tenant_id=tenant_id, detected_at=now,
            severity=severity, kind=kind, title=title, summary=summary,
            affected_subjects_estimate=affected_subjects_estimate,
            data_classes=data_classes, jurisdictions=jurisdictions,
            notifications_due=due, metadata=metadata or {},
        )
        self._items[bid] = r
        return r

    def get(self, breach_id: str) -> Optional[BreachReport]:
        return self._items.get(breach_id)

    def mark_notified(self, breach_id: str, window_id: str) -> Optional[BreachReport]:
        r = self._items.get(breach_id)
        if r is None:
            return None
        r.notifications_sent[window_id] = time.time()
        return r

    def close(self, breach_id: str, rcca_uri: Optional[str] = None) -> Optional[BreachReport]:
        r = self._items.get(breach_id)
        if r is None:
            return None
        r.closed_at = time.time()
        if rcca_uri:
            r.rcca_uri = rcca_uri
        return r

    def all(self, tenant_id: Optional[str] = None) -> list[BreachReport]:
        out = list(self._items.values())
        if tenant_id is not None:
            out = [r for r in out if r.tenant_id == tenant_id]
        out.sort(key=lambda r: r.detected_at, reverse=True)
        return out

    def overdue(self) -> list[tuple[BreachReport, str, float]]:
        """Return tuples of (breach, window_id, hours_overdue)."""
        out: list[tuple[BreachReport, str, float]] = []
        now = time.time()
        for r in self._items.values():
            if r.closed_at is not None:
                continue
            for w, deadline in r.notifications_due.items():
                if w in r.notifications_sent:
                    continue
                if now > deadline:
                    out.append((r, w, (now - deadline) / 3600.0))
        out.sort(key=lambda t: -t[2])
        return out


def classify(severity: str, affected_subjects_estimate: Optional[int]) -> str:
    """Heuristic mapping to DORA / GDPR "major" thresholds.

    DORA Art.18 considers an incident "major" when service-affecting and
    impacting customers; GDPR Art.33 considers a breach reportable when
    likely to result in a risk to rights and freedoms. Our defaults:

        critical  → "major" always
        high + ≥1 affected subject → "major"
        otherwise → "minor"
    """
    if severity == BreachSeverity.CRITICAL.value:
        return "major"
    if severity == BreachSeverity.HIGH.value and (affected_subjects_estimate or 0) > 0:
        return "major"
    return "minor"
