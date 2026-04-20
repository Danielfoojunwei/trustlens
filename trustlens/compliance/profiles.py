"""Per-tenant compliance profile.

Captures which regulations the tenant is subject to plus the metadata
needed to satisfy them — DPO contact, lawful basis, residency, retention
overrides, jurisdictions for breach reporting, etc.

The profile is the input to ``transparency.generate_ropa()`` and to
``frameworks.overall_status()`` filtered by tenant.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Optional, Protocol


@dataclass
class TenantComplianceProfile:
    tenant_id: str
    legal_name: str = ""
    address: str = ""
    dpo_contact: Optional[str] = None
    representative_eu: Optional[str] = None      # GDPR Art.27
    lawful_basis: str = "contract"               # GDPR Art.6 default
    purposes_of_processing: list[str] = field(default_factory=list)
    categories_of_data: list[str] = field(default_factory=list)
    categories_of_subjects: list[str] = field(default_factory=list)
    deployment_geographies: list[str] = field(default_factory=list)
    data_residency: list[str] = field(default_factory=list)
    cross_border_basis: list[str] = field(default_factory=list)   # SCC / adequacy / DPF
    sub_processors: list[str] = field(default_factory=list)
    applicable_frameworks: list[str] = field(default_factory=list) # FrameworkId values
    breach_reporting_jurisdictions: list[str] = field(default_factory=list)
    sensitive_pi: bool = False
    is_significant_data_fiduciary: bool = False  # India DPDP Sec.10
    is_high_risk_ai: bool = False                 # EU AI Act Annex III
    notes: str = ""
    updated_at: float = field(default_factory=time.time)
    updated_by: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class ProfileStore(Protocol):
    def get(self, tenant_id: str) -> Optional[TenantComplianceProfile]: ...
    def upsert(self, profile: TenantComplianceProfile) -> None: ...
    def all(self) -> list[TenantComplianceProfile]: ...
    def delete(self, tenant_id: str) -> bool: ...


class InMemoryProfileStore:
    def __init__(self) -> None:
        self._by_tenant: dict[str, TenantComplianceProfile] = {}

    def get(self, tenant_id: str) -> Optional[TenantComplianceProfile]:
        return self._by_tenant.get(tenant_id)

    def upsert(self, profile: TenantComplianceProfile) -> None:
        profile.updated_at = time.time()
        self._by_tenant[profile.tenant_id] = profile

    def all(self) -> list[TenantComplianceProfile]:
        return list(self._by_tenant.values())

    def delete(self, tenant_id: str) -> bool:
        return self._by_tenant.pop(tenant_id, None) is not None


def starter_profile(tenant_id: str) -> TenantComplianceProfile:
    return TenantComplianceProfile(
        tenant_id=tenant_id,
        applicable_frameworks=["gdpr", "iso_27001", "iso_42001",
                                "nist_ai_rmf", "soc_2"],
        purposes_of_processing=["service_delivery"],
        categories_of_data=["account_metadata", "prompts", "responses"],
        categories_of_subjects=["end_users", "operators"],
        deployment_geographies=["EU"],
        data_residency=["EU"],
        breach_reporting_jurisdictions=["gdpr"],
    )
