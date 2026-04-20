"""Model cards (ISO 42001 A.6.1.2, EU AI Act Art.11/13, NIST AI RMF MAP).

A model card describes the AI system the tenant ships:

    - intended use + out-of-scope use
    - training data lineage (what TrustLens *verifies against*; we
      typically don't train models, we verify them)
    - performance metrics (link to signed scorecards)
    - known limitations + failure modes
    - human oversight + escalation
    - contact + version

Cards are versioned; the dashboard shows the version timeline.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Optional, Protocol


@dataclass
class ModelCardVersion:
    version: int
    committed_at: float
    committed_by: Optional[str]
    summary: str
    body: dict                           # immutable snapshot of the card body


@dataclass
class ModelCard:
    card_id: str
    tenant_id: Optional[str]
    system_name: str
    provider: str                        # e.g. "OpenAI", "Anthropic", "in-house"
    model_id: str                        # e.g. "gpt-4o-2024-05"
    intended_use: str
    out_of_scope_use: list[str] = field(default_factory=list)
    user_groups: list[str] = field(default_factory=list)        # e.g. "internal-employees"
    deployment_geographies: list[str] = field(default_factory=list)
    training_data_summary: str = ""
    evaluation_data_summary: str = ""
    performance_metrics: dict = field(default_factory=dict)     # link to scorecard ids
    risks: list[str] = field(default_factory=list)              # risk_id list
    mitigations: list[str] = field(default_factory=list)
    human_oversight: str = ""
    monitoring_plan: str = ""
    contact: str = ""                    # responsible owner email/team
    framework_refs: list[str] = field(default_factory=list)
    versions: list[ModelCardVersion] = field(default_factory=list)
    current_version: int = 1
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["versions"] = [asdict(v) for v in self.versions]
        return d

    def snapshot_body(self) -> dict:
        return {
            "system_name": self.system_name, "provider": self.provider,
            "model_id": self.model_id, "intended_use": self.intended_use,
            "out_of_scope_use": list(self.out_of_scope_use),
            "user_groups": list(self.user_groups),
            "deployment_geographies": list(self.deployment_geographies),
            "training_data_summary": self.training_data_summary,
            "evaluation_data_summary": self.evaluation_data_summary,
            "performance_metrics": dict(self.performance_metrics),
            "risks": list(self.risks),
            "mitigations": list(self.mitigations),
            "human_oversight": self.human_oversight,
            "monitoring_plan": self.monitoring_plan,
            "contact": self.contact,
            "framework_refs": list(self.framework_refs),
        }


class ModelCardStore(Protocol):
    def create(self, card: ModelCard, committed_by: Optional[str] = None) -> ModelCard: ...
    def get(self, card_id: str) -> Optional[ModelCard]: ...
    def update(self, card_id: str, *, summary: str,
               committed_by: Optional[str] = None, **fields) -> Optional[ModelCard]: ...
    def all(self, tenant_id: Optional[str] = None) -> list[ModelCard]: ...
    def delete(self, card_id: str) -> bool: ...


class InMemoryModelCardStore:
    def __init__(self) -> None:
        self._cards: dict[str, ModelCard] = {}

    def create(self, card: ModelCard, committed_by: Optional[str] = None) -> ModelCard:
        if not card.card_id:
            card.card_id = "mc_" + secrets.token_hex(6)
        card.versions = [ModelCardVersion(
            version=1, committed_at=time.time(), committed_by=committed_by,
            summary="initial", body=card.snapshot_body(),
        )]
        card.current_version = 1
        self._cards[card.card_id] = card
        return card

    def get(self, card_id: str) -> Optional[ModelCard]:
        return self._cards.get(card_id)

    def update(self, card_id: str, *, summary: str,
               committed_by: Optional[str] = None, **fields) -> Optional[ModelCard]:
        c = self._cards.get(card_id)
        if c is None:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(c, k):
                setattr(c, k, v)
        c.current_version += 1
        c.versions.append(ModelCardVersion(
            version=c.current_version, committed_at=time.time(),
            committed_by=committed_by, summary=summary,
            body=c.snapshot_body(),
        ))
        return c

    def all(self, tenant_id: Optional[str] = None) -> list[ModelCard]:
        out = list(self._cards.values())
        if tenant_id is not None:
            out = [c for c in out if c.tenant_id == tenant_id]
        out.sort(key=lambda c: c.created_at, reverse=True)
        return out

    def delete(self, card_id: str) -> bool:
        return self._cards.pop(card_id, None) is not None
