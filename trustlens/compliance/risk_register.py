"""AI risk register + impact assessments (DPIA / AIIA / Algorithmic IA).

Used by:
    - GDPR Art.35      DPIA for high-risk processing
    - EU AI Act Art.9  Risk management system
    - ISO 42001 8.4    AI system impact assessment
    - NIST AI RMF      Map / Measure / Manage functions
    - Colorado AI Act  Algorithmic Impact Assessment (AIA)
    - DORA Art.5/9     ICT risk identification
"""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional, Protocol


class RiskCategory(str, Enum):
    HALLUCINATION       = "hallucination"
    PROMPT_INJECTION    = "prompt_injection"
    PII_LEAK            = "pii_leak"
    DISCRIMINATION      = "discrimination"        # bias / unfair output
    AUTOMATION_BIAS     = "automation_bias"        # human over-trust of AI
    OPACITY             = "opacity"                # explainability
    DATA_POISONING      = "data_poisoning"
    MODEL_INVERSION     = "model_inversion"
    JAILBREAK           = "jailbreak"
    AVAILABILITY        = "availability"
    SUPPLY_CHAIN        = "supply_chain"
    REGULATORY          = "regulatory"


class Likelihood(str, Enum):
    RARE     = "rare"
    UNLIKELY = "unlikely"
    POSSIBLE = "possible"
    LIKELY   = "likely"
    ALMOST   = "almost_certain"


class Impact(str, Enum):
    NEGLIGIBLE = "negligible"
    MINOR      = "minor"
    MODERATE   = "moderate"
    MAJOR      = "major"
    SEVERE     = "severe"


_LIKE_SCORE  = {"rare": 1, "unlikely": 2, "possible": 3, "likely": 4, "almost_certain": 5}
_IMP_SCORE   = {"negligible": 1, "minor": 2, "moderate": 3, "major": 4, "severe": 5}


def risk_score(likelihood: str, impact: str) -> int:
    return _LIKE_SCORE.get(likelihood, 3) * _IMP_SCORE.get(impact, 3)


@dataclass
class RiskItem:
    risk_id: str
    tenant_id: Optional[str]
    category: str
    title: str
    description: str
    likelihood: str
    impact: str
    score: int
    inherent_score: int                   # before controls
    residual_score: int                   # after controls
    controls: list[str] = field(default_factory=list)        # references to controls
    owner: Optional[str] = None
    status: str = "open"                  # open | accepted | mitigated | closed
    framework_refs: list[str] = field(default_factory=list)  # e.g. ["EUAI.Art.9", "NIST.MP-5.1"]
    created_at: float = field(default_factory=time.time)
    last_review_at: float = field(default_factory=time.time)
    next_review_at: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AIIAReport:
    """Algorithmic / AI Impact Assessment (EU AI Act Art.9, Colorado AIA, ISO 42001 8.4)."""
    report_id: str
    tenant_id: Optional[str]
    system_name: str
    intended_purpose: str
    risk_classification: str              # "minimal" | "limited" | "high" | "unacceptable"
    affected_groups: list[str]
    deployed_geographies: list[str]
    risks: list[str]                      # risk_id list
    mitigations: list[str]
    human_oversight_summary: str
    monitoring_summary: str
    sign_off_by: Optional[str]
    sign_off_at: Optional[float]
    next_review_at: Optional[float]
    framework_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class RiskStore(Protocol):
    def add(self, item: RiskItem) -> RiskItem: ...
    def update(self, risk_id: str, **fields) -> Optional[RiskItem]: ...
    def get(self, risk_id: str) -> Optional[RiskItem]: ...
    def all(self, tenant_id: Optional[str] = None,
            status: Optional[str] = None) -> list[RiskItem]: ...
    def add_aiia(self, report: AIIAReport) -> AIIAReport: ...
    def aiias(self, tenant_id: Optional[str] = None) -> list[AIIAReport]: ...


class InMemoryRiskStore:
    def __init__(self) -> None:
        self._risks: dict[str, RiskItem] = {}
        self._aiias: dict[str, AIIAReport] = {}

    def add(self, item: RiskItem) -> RiskItem:
        self._risks[item.risk_id] = item
        return item

    def update(self, risk_id: str, **fields) -> Optional[RiskItem]:
        r = self._risks.get(risk_id)
        if r is None:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(r, k):
                setattr(r, k, v)
        if "likelihood" in fields or "impact" in fields:
            r.score = risk_score(r.likelihood, r.impact)
            r.residual_score = r.score
        r.last_review_at = time.time()
        return r

    def get(self, risk_id: str) -> Optional[RiskItem]:
        return self._risks.get(risk_id)

    def all(self, tenant_id: Optional[str] = None,
            status: Optional[str] = None) -> list[RiskItem]:
        out = list(self._risks.values())
        if tenant_id is not None:
            out = [r for r in out if r.tenant_id == tenant_id]
        if status is not None:
            out = [r for r in out if r.status == status]
        out.sort(key=lambda r: r.score, reverse=True)
        return out

    def add_aiia(self, report: AIIAReport) -> AIIAReport:
        self._aiias[report.report_id] = report
        return report

    def aiias(self, tenant_id: Optional[str] = None) -> list[AIIAReport]:
        out = list(self._aiias.values())
        if tenant_id is not None:
            out = [r for r in out if r.tenant_id == tenant_id]
        out.sort(key=lambda r: r.report_id, reverse=True)
        return out


def is_high_risk_eu_ai_act(intended_purpose: str,
                            deployed_geographies: list[str]) -> bool:
    """Best-effort EU AI Act high-risk classifier (Annex III).

    Returns True for typical high-risk categories: employment screening,
    credit scoring, education evaluation, critical infrastructure,
    law enforcement, migration. Operators MUST review this with counsel.
    """
    if not any(g.upper() in {"EU", "EEA"} for g in deployed_geographies):
        return False
    p = intended_purpose.lower()
    high_risk_keywords = (
        "employment", "hiring", "recruiting", "promotion",
        "credit", "loan", "mortgage", "insurance",
        "education", "exam", "admission",
        "law enforcement", "migration", "asylum", "border",
        "critical infrastructure", "biometric",
        "social scoring", "essential service",
    )
    return any(k in p for k in high_risk_keywords)


def seed_default_risks(tenant_id: Optional[str] = None) -> list[RiskItem]:
    """Reasonable starter risks for any LLM-in-prod deployment."""
    common = [
        ("hallucination",   "Hallucinated factual claim reaches end-user",
         "likely", "moderate", ["EUAI.Art.9", "ISO42001.8.2", "NIST.MP-5.1"]),
        ("prompt_injection", "Instruction injection from third-party content",
         "possible", "major", ["EUAI.Art.15", "ISO42001.8.3"]),
        ("pii_leak", "Model reveals PII present in retrieval context",
         "possible", "major", ["GDPR.Art.32", "ISO27701.A.7.4.1"]),
        ("discrimination", "Discriminatory output against protected class",
         "unlikely", "severe", ["EUAI.Art.10", "COAI.6-1-1702"]),
        ("automation_bias", "Operator over-trusts AI output without review",
         "possible", "moderate", ["EUAI.Art.14", "ISO42001.8.3"]),
        ("opacity", "Insufficient explainability for end-user decisions",
         "possible", "moderate", ["EUAI.Art.13", "KR.AI.Art.16"]),
    ]
    out: list[RiskItem] = []
    for cat, title, lhood, imp, refs in common:
        s = risk_score(lhood, imp)
        out.append(RiskItem(
            risk_id="risk_" + secrets.token_hex(5), tenant_id=tenant_id,
            category=cat, title=title, description=title,
            likelihood=lhood, impact=imp, score=s,
            inherent_score=s, residual_score=s,
            framework_refs=refs,
        ))
    return out
