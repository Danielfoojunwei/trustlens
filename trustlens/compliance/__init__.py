"""TrustLens compliance subsystem.

Single import surface for: framework catalog, audit log, consent, DSAR,
retention, breach, AI risk register + AIIA, model cards, tenant
compliance profiles, transparency artifact generators.
"""

from trustlens.compliance.frameworks import (
    COMPLIANCE_VERSION, ControlStatus, Framework, FrameworkId,
    all_frameworks, get_framework, overall_status,
)
from trustlens.compliance.audit_log import (
    AuditEvent, AuditLogStore, ChainVerifyResult,
    FilesystemAuditLog, InMemoryAuditLog, export_csv, export_jsonl,
)
from trustlens.compliance.consent import (
    ConsentPurpose, ConsentRecord, ConsentStatus, ConsentStore,
    InMemoryConsentStore,
)
from trustlens.compliance.dsar import (
    DSARRequest, DSARRequestType, DSARStatus, DSARStore,
    InMemoryDSARStore, SLA_DAYS,
)
from trustlens.compliance.retention import (
    DataClass, InMemoryRetentionStore, RetentionPolicy, RetentionStore,
    compute_due, default_policies,
)
from trustlens.compliance.breach import (
    BreachKind, BreachReport, BreachSeverity, BreachStore,
    InMemoryBreachStore, REPORTING_WINDOWS_HOURS, classify,
)
from trustlens.compliance.risk_register import (
    AIIAReport, Impact, InMemoryRiskStore, Likelihood, RiskCategory,
    RiskItem, RiskStore, is_high_risk_eu_ai_act, risk_score,
    seed_default_risks,
)
from trustlens.compliance.model_cards import (
    InMemoryModelCardStore, ModelCard, ModelCardStore, ModelCardVersion,
)
from trustlens.compliance.profiles import (
    InMemoryProfileStore, ProfileStore, TenantComplianceProfile,
    starter_profile,
)
from trustlens.compliance.transparency import (
    generate_compliance_overview, generate_consent_summary,
    generate_dsar_summary, generate_eu_ai_act_summary,
    generate_privacy_notice, generate_ropa,
)

__all__ = [
    # frameworks
    "COMPLIANCE_VERSION", "ControlStatus", "Framework", "FrameworkId",
    "all_frameworks", "get_framework", "overall_status",
    # audit log
    "AuditEvent", "AuditLogStore", "ChainVerifyResult",
    "FilesystemAuditLog", "InMemoryAuditLog", "export_csv", "export_jsonl",
    # consent
    "ConsentPurpose", "ConsentRecord", "ConsentStatus", "ConsentStore",
    "InMemoryConsentStore",
    # dsar
    "DSARRequest", "DSARRequestType", "DSARStatus", "DSARStore",
    "InMemoryDSARStore", "SLA_DAYS",
    # retention
    "DataClass", "InMemoryRetentionStore", "RetentionPolicy", "RetentionStore",
    "compute_due", "default_policies",
    # breach
    "BreachKind", "BreachReport", "BreachSeverity", "BreachStore",
    "InMemoryBreachStore", "REPORTING_WINDOWS_HOURS", "classify",
    # risk
    "AIIAReport", "Impact", "InMemoryRiskStore", "Likelihood", "RiskCategory",
    "RiskItem", "RiskStore", "is_high_risk_eu_ai_act", "risk_score",
    "seed_default_risks",
    # model cards
    "InMemoryModelCardStore", "ModelCard", "ModelCardStore", "ModelCardVersion",
    # profiles
    "InMemoryProfileStore", "ProfileStore", "TenantComplianceProfile",
    "starter_profile",
    # transparency
    "generate_compliance_overview", "generate_consent_summary",
    "generate_dsar_summary", "generate_eu_ai_act_summary",
    "generate_privacy_notice", "generate_ropa",
]
