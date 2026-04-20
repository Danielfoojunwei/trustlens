"""Catalog of regulatory frameworks TrustLens supports.

Each ``Framework`` lists the controls TrustLens implements (or partially
implements) and the SLA values the rest of the compliance subsystem reads.

Sources cited inline are the article / clause numbers the specific
control maps to. The dashboard's COMPLIANCE OVERVIEW renders a
red/yellow/green grid by walking this catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


COMPLIANCE_VERSION = "compliance/1.0.0"


class FrameworkId(str, Enum):
    GDPR             = "gdpr"
    CCPA             = "ccpa"
    ISO_27001        = "iso_27001"
    ISO_27701        = "iso_27701"
    ISO_42001        = "iso_42001"
    ISO_22989        = "iso_22989"
    ISO_23053        = "iso_23053"
    EU_AI_ACT        = "eu_ai_act"
    NIST_AI_RMF      = "nist_ai_rmf"
    SOC_2            = "soc_2"
    DORA             = "dora"
    COLORADO_AI      = "colorado_ai"
    INDIA_DPDP       = "india_dpdp"
    CHINA_GENAI      = "china_genai"
    KOREA_AI         = "korea_ai"
    HIPAA            = "hipaa"
    FEDRAMP_MOD      = "fedramp_moderate"
    PCI_DSS_4        = "pci_dss_4"


class ControlStatus(str, Enum):
    SHIP    = "ship"     # implemented today, evidence available
    PARTIAL = "partial"  # in code but not fully wired / evidenced
    NEAR    = "near"     # roadmap, single-PR scope
    GAP     = "gap"      # not yet started


@dataclass
class Control:
    control_id: str            # e.g. "GDPR.Art.30"
    title: str
    status: ControlStatus
    evidence: list[str] = field(default_factory=list)   # references / file paths
    notes: str = ""


@dataclass
class Framework:
    id: FrameworkId
    name: str
    short_name: str
    jurisdiction: str
    summary: str
    sla: dict = field(default_factory=dict)             # e.g. {"dsar_response_days": 30}
    controls: list[Control] = field(default_factory=list)

    def status_summary(self) -> dict:
        from collections import Counter
        c = Counter(ctl.status.value for ctl in self.controls)
        return {
            "total":   len(self.controls),
            "ship":    c.get("ship", 0),
            "partial": c.get("partial", 0),
            "near":    c.get("near", 0),
            "gap":     c.get("gap", 0),
            "score":   round(
                (c.get("ship", 0) + 0.5 * c.get("partial", 0)) /
                max(len(self.controls), 1), 3,
            ),
        }


# ---------------------------------------------------------------------------
# Catalog — keep concise; one-line evidence per control
# ---------------------------------------------------------------------------

_CTL = ControlStatus  # short alias


def _gdpr() -> Framework:
    return Framework(
        id=FrameworkId.GDPR, name="EU General Data Protection Regulation",
        short_name="GDPR", jurisdiction="EU/EEA",
        summary="Comprehensive personal data protection law applicable to controllers + processors handling EU data.",
        sla={"dsar_response_days": 30, "breach_notification_hours": 72,
             "default_retention_days": 365},
        controls=[
            Control("GDPR.Art.5",  "Lawfulness/fairness/purpose limitation/storage limitation/integrity",   _CTL.PARTIAL,
                    ["compliance/profiles.py", "compliance/retention.py"]),
            Control("GDPR.Art.6",  "Lawful basis for processing (per-tenant declared)",                     _CTL.SHIP,
                    ["compliance/profiles.py::TenantComplianceProfile.lawful_basis"]),
            Control("GDPR.Art.7",  "Conditions for consent (proof + withdrawal)",                            _CTL.SHIP,
                    ["compliance/consent.py"]),
            Control("GDPR.Art.12", "Transparent information + DSAR SLA",                                     _CTL.SHIP,
                    ["compliance/dsar.py"]),
            Control("GDPR.Art.15", "Right of access (export structured copy)",                               _CTL.SHIP,
                    ["compliance/dsar.py::DSARRequestType.ACCESS"]),
            Control("GDPR.Art.16", "Right to rectification",                                                 _CTL.SHIP,
                    ["compliance/dsar.py::DSARRequestType.RECTIFY"]),
            Control("GDPR.Art.17", "Right to erasure (right to be forgotten)",                               _CTL.SHIP,
                    ["compliance/dsar.py::DSARRequestType.DELETE"]),
            Control("GDPR.Art.20", "Right to data portability (machine-readable)",                           _CTL.SHIP,
                    ["compliance/dsar.py::DSARRequestType.PORTABILITY"]),
            Control("GDPR.Art.22", "Automated decision-making (right to human review)",                      _CTL.PARTIAL,
                    ["verifier/router.py", "incidents/__init__.py"]),
            Control("GDPR.Art.25", "Data protection by design & default",                                    _CTL.SHIP,
                    ["whole-product"]),
            Control("GDPR.Art.30", "Records of processing activities (RoPA)",                                _CTL.SHIP,
                    ["compliance/transparency.py::generate_ropa"]),
            Control("GDPR.Art.32", "Security of processing (encryption / access control)",                   _CTL.PARTIAL,
                    ["auth/", "certificate/signer.py"]),
            Control("GDPR.Art.33", "Notification of breach to authority within 72h",                          _CTL.SHIP,
                    ["compliance/breach.py"]),
            Control("GDPR.Art.34", "Communication of breach to data subject",                                 _CTL.SHIP,
                    ["compliance/breach.py"]),
            Control("GDPR.Art.35", "DPIA for high-risk processing",                                           _CTL.SHIP,
                    ["compliance/risk_register.py"]),
            Control("GDPR.Art.37", "Designation of DPO",                                                       _CTL.SHIP,
                    ["compliance/profiles.py::TenantComplianceProfile.dpo_contact"]),
            Control("GDPR.Art.44-49", "Cross-border transfers (SCC / adequacy / TIA)",                        _CTL.NEAR,
                    [], "Per-tenant residency hint exists; SCC ledger NEAR-term"),
        ],
    )


def _ccpa() -> Framework:
    return Framework(
        id=FrameworkId.CCPA, name="California Consumer Privacy Act / CPRA",
        short_name="CCPA/CPRA", jurisdiction="US-California",
        summary="California consumer rights with respect to personal information.",
        sla={"dsar_response_days": 45, "breach_notification_days": 45,
             "default_retention_days": 365},
        controls=[
            Control("CCPA.1798.100", "Right to know",                                _CTL.SHIP, ["compliance/dsar.py"]),
            Control("CCPA.1798.105", "Right to delete",                              _CTL.SHIP, ["compliance/dsar.py"]),
            Control("CCPA.1798.106", "Right to correct",                             _CTL.SHIP, ["compliance/dsar.py"]),
            Control("CCPA.1798.110", "Right to know specific PI",                    _CTL.SHIP, ["compliance/dsar.py"]),
            Control("CCPA.1798.120", "Right to opt-out of sale / sharing",           _CTL.SHIP, ["compliance/consent.py"]),
            Control("CCPA.1798.121", "Right to limit use of sensitive PI",           _CTL.PARTIAL, ["compliance/profiles.py"]),
            Control("CCPA.1798.130", "Notice at collection",                         _CTL.SHIP, ["compliance/transparency.py::privacy_notice"]),
            Control("CCPA.1798.150", "Breach notification (45d)",                    _CTL.SHIP, ["compliance/breach.py"]),
        ],
    )


def _iso27001() -> Framework:
    return Framework(
        id=FrameworkId.ISO_27001, name="ISO/IEC 27001:2022 Information Security Management System",
        short_name="ISO 27001", jurisdiction="International",
        summary="Risk-based information security management system standard.",
        sla={"control_review_days": 365},
        controls=[
            Control("ISO27001.A.5.1",  "Policies for information security",   _CTL.SHIP,    ["docs/OPERATIONS.md"]),
            Control("ISO27001.A.5.15", "Access control",                       _CTL.SHIP,    ["auth/rbac.py"]),
            Control("ISO27001.A.5.23", "Information security for cloud",       _CTL.PARTIAL, ["docs/ENTERPRISE.md"]),
            Control("ISO27001.A.5.24", "Incident management planning",         _CTL.SHIP,    ["incidents/__init__.py"]),
            Control("ISO27001.A.5.25", "Incident assessment & decision",       _CTL.SHIP,    ["compliance/breach.py"]),
            Control("ISO27001.A.5.26", "Response to incidents",                _CTL.SHIP,    ["incidents/__init__.py"]),
            Control("ISO27001.A.5.27", "Learning from incidents",              _CTL.PARTIAL, ["incidents/__init__.py"]),
            Control("ISO27001.A.5.28", "Collection of evidence",               _CTL.SHIP,    ["compliance/audit_log.py"]),
            Control("ISO27001.A.5.30", "ICT readiness for continuity",         _CTL.SHIP,    ["robustness/circuit_breaker.py"]),
            Control("ISO27001.A.6.3",  "Awareness, education, training",       _CTL.NEAR,    [], "Training records NEAR-term"),
            Control("ISO27001.A.8.2",  "Privileged access rights",             _CTL.SHIP,    ["auth/rbac.py"]),
            Control("ISO27001.A.8.5",  "Secure authentication",                _CTL.SHIP,    ["auth/users.py", "auth/sessions.py"]),
            Control("ISO27001.A.8.10", "Information deletion",                 _CTL.SHIP,    ["compliance/retention.py"]),
            Control("ISO27001.A.8.12", "Data leakage prevention",              _CTL.PARTIAL, ["verifier/router.py"]),
            Control("ISO27001.A.8.15", "Logging",                              _CTL.SHIP,    ["compliance/audit_log.py", "observability/metrics.py"]),
            Control("ISO27001.A.8.16", "Monitoring activities",                _CTL.SHIP,    ["observability/metrics.py", "verifier/axes.py"]),
            Control("ISO27001.A.8.17", "Clock synchronization",                _CTL.SHIP,    ["NTP-assumed-on-host"]),
            Control("ISO27001.A.8.24", "Use of cryptography",                  _CTL.SHIP,    ["certificate/signer.py"]),
            Control("ISO27001.A.8.28", "Secure coding",                        _CTL.PARTIAL, ["tests/"]),
            Control("ISO27001.A.8.30", "Outsourced development",               _CTL.NEAR,    []),
            Control("ISO27001.A.8.31", "Separation of dev/test/prod",          _CTL.PARTIAL, ["tests/"]),
            Control("ISO27001.A.8.32", "Change management",                    _CTL.SHIP,    ["compliance/audit_log.py"]),
        ],
    )


def _iso27701() -> Framework:
    return Framework(
        id=FrameworkId.ISO_27701, name="ISO/IEC 27701:2019 Privacy Information Management System",
        short_name="ISO 27701", jurisdiction="International",
        summary="Extension of ISO 27001 for privacy information management.",
        sla={},
        controls=[
            Control("ISO27701.A.7.2.1", "Identify and document purpose",         _CTL.SHIP,   ["compliance/profiles.py"]),
            Control("ISO27701.A.7.2.2", "Identify lawful basis",                 _CTL.SHIP,   ["compliance/profiles.py"]),
            Control("ISO27701.A.7.2.3", "Determine when and how consent",        _CTL.SHIP,   ["compliance/consent.py"]),
            Control("ISO27701.A.7.2.4", "Obtain and record consent",             _CTL.SHIP,   ["compliance/consent.py"]),
            Control("ISO27701.A.7.2.5", "PIA / DPIA",                            _CTL.SHIP,   ["compliance/risk_register.py"]),
            Control("ISO27701.A.7.3.1", "Determine PII processor",               _CTL.SHIP,   ["compliance/profiles.py"]),
            Control("ISO27701.A.7.3.2", "Information for PII principals",        _CTL.SHIP,   ["compliance/transparency.py"]),
            Control("ISO27701.A.7.3.3", "Providing mechanism to modify consent", _CTL.SHIP,   ["compliance/consent.py"]),
            Control("ISO27701.A.7.4.1", "Limit collection",                      _CTL.PARTIAL,["verifier/router.py"]),
            Control("ISO27701.A.7.4.5", "PII de-identification at end of processing", _CTL.SHIP, ["compliance/retention.py"]),
            Control("ISO27701.A.7.5.1", "Identify basis for transfer",            _CTL.NEAR,  []),
            Control("ISO27701.A.8.2.1", "Customer agreement (DPA)",              _CTL.NEAR,  []),
        ],
    )


def _iso42001() -> Framework:
    return Framework(
        id=FrameworkId.ISO_42001, name="ISO/IEC 42001:2023 AI Management System",
        short_name="ISO 42001", jurisdiction="International",
        summary="Management system standard for organizations using AI.",
        sla={"risk_review_days": 90, "model_card_review_days": 180},
        controls=[
            Control("ISO42001.5.1",  "Leadership and commitment",           _CTL.PARTIAL, ["docs/OPERATIONS.md"]),
            Control("ISO42001.5.2",  "AI policy",                            _CTL.PARTIAL, ["docs/OPERATIONS.md"]),
            Control("ISO42001.6.1",  "Risk-based approach",                  _CTL.SHIP,    ["compliance/risk_register.py"]),
            Control("ISO42001.6.2",  "AI objectives & planning",             _CTL.PARTIAL, ["docs/ENTERPRISE.md"]),
            Control("ISO42001.7.4",  "Communication",                         _CTL.SHIP,    ["compliance/transparency.py"]),
            Control("ISO42001.8.2",  "AI risk assessment",                    _CTL.SHIP,    ["compliance/risk_register.py"]),
            Control("ISO42001.8.3",  "AI risk treatment",                     _CTL.SHIP,    ["verifier/", "deep_inspector/"]),
            Control("ISO42001.8.4",  "AI system impact assessment",           _CTL.SHIP,    ["compliance/risk_register.py::AIIAReport"]),
            Control("ISO42001.9.1",  "Monitoring, measurement, evaluation",   _CTL.SHIP,    ["observability/", "verifier/axes.py"]),
            Control("ISO42001.9.2",  "Internal audit",                        _CTL.SHIP,    ["compliance/audit_log.py"]),
            Control("ISO42001.10.2", "Nonconformity & corrective action",     _CTL.SHIP,    ["incidents/__init__.py"]),
            Control("ISO42001.A.6.1.2", "AI system documentation (model card)", _CTL.SHIP,  ["compliance/model_cards.py"]),
            Control("ISO42001.A.7.2",   "Data resources for AI",              _CTL.SHIP,    ["kb/versioning.py"]),
            Control("ISO42001.A.7.4",   "Quality of data for AI",             _CTL.PARTIAL, ["verifier/calibration.py"]),
            Control("ISO42001.A.8.2",   "System impact assessment",           _CTL.SHIP,    ["compliance/risk_register.py"]),
            Control("ISO42001.A.9.2",   "Reporting AI incidents",             _CTL.SHIP,    ["incidents/", "compliance/breach.py"]),
        ],
    )


def _eu_ai_act() -> Framework:
    return Framework(
        id=FrameworkId.EU_AI_ACT, name="EU AI Act (Regulation 2024/1689)",
        short_name="EU AI Act", jurisdiction="EU/EEA",
        summary="Risk-based binding rules for AI systems with high-risk obligations.",
        sla={"post_market_monitoring_days": 90, "serious_incident_report_days": 15},
        controls=[
            Control("EUAI.Art.9",  "Risk management system",                       _CTL.SHIP,    ["compliance/risk_register.py"]),
            Control("EUAI.Art.10", "Data and data governance",                      _CTL.PARTIAL, ["kb/versioning.py", "compliance/model_cards.py"]),
            Control("EUAI.Art.11", "Technical documentation",                        _CTL.SHIP,    ["compliance/model_cards.py", "compliance/transparency.py"]),
            Control("EUAI.Art.12", "Record-keeping (logs)",                          _CTL.SHIP,    ["compliance/audit_log.py"]),
            Control("EUAI.Art.13", "Transparency & provision of information to deployers", _CTL.SHIP, ["compliance/transparency.py"]),
            Control("EUAI.Art.14", "Human oversight",                                _CTL.PARTIAL, ["verifier/router.py", "incidents/"]),
            Control("EUAI.Art.15", "Accuracy, robustness, cybersecurity",            _CTL.SHIP,    ["deep_inspector/benchmarks/", "verifier/calibration.py"]),
            Control("EUAI.Art.17", "Quality management system",                      _CTL.SHIP,    ["tests/", "compliance/audit_log.py"]),
            Control("EUAI.Art.26", "Obligations of deployers",                       _CTL.PARTIAL, ["compliance/transparency.py"]),
            Control("EUAI.Art.50", "Transparency obligations for AI-generated content", _CTL.NEAR, [], "Watermark/labeling NEAR-term"),
            Control("EUAI.Art.72", "Post-market monitoring",                         _CTL.SHIP,    ["robustness/shadow_eval.py", "observability/"]),
            Control("EUAI.Art.73", "Reporting of serious incidents",                 _CTL.SHIP,    ["compliance/breach.py", "incidents/"]),
        ],
    )


def _nist_ai_rmf() -> Framework:
    return Framework(
        id=FrameworkId.NIST_AI_RMF, name="NIST AI Risk Management Framework 2.0",
        short_name="NIST AI RMF", jurisdiction="US (voluntary)",
        summary="Govern → Map → Measure → Manage cycle for AI risk.",
        sla={},
        controls=[
            Control("NIST.GV-1.1", "Legal/regulatory requirements managed",   _CTL.SHIP,    ["compliance/profiles.py"]),
            Control("NIST.GV-1.2", "Trustworthy AI characteristics integrated", _CTL.SHIP,  ["verifier/", "deep_inspector/"]),
            Control("NIST.GV-1.3", "Risk-management processes",                _CTL.SHIP,    ["compliance/risk_register.py"]),
            Control("NIST.GV-1.4", "Risk-management roles and responsibilities", _CTL.SHIP, ["auth/rbac.py"]),
            Control("NIST.GV-1.5", "Ongoing monitoring & evaluation",          _CTL.SHIP,    ["robustness/shadow_eval.py"]),
            Control("NIST.GV-3.2", "Trustworthy & responsible AI culture",      _CTL.PARTIAL, []),
            Control("NIST.MP-1.1", "Intended purposes & potential impact",      _CTL.SHIP,    ["compliance/model_cards.py"]),
            Control("NIST.MP-2.1", "Tasks and methods documented",              _CTL.SHIP,    ["compliance/model_cards.py"]),
            Control("NIST.MP-3.1", "Capabilities/limitations described",        _CTL.SHIP,    ["compliance/model_cards.py"]),
            Control("NIST.MP-4.1", "Human roles defined",                        _CTL.SHIP,    ["auth/rbac.py"]),
            Control("NIST.MP-5.1", "Likelihood and magnitude of impact",        _CTL.SHIP,    ["compliance/risk_register.py"]),
            Control("NIST.ME-1.1", "Approaches & metrics identified",           _CTL.SHIP,    ["observability/", "deep_inspector/benchmarks/"]),
            Control("NIST.ME-2.1", "Test sets, metrics, demographics",          _CTL.PARTIAL, ["deep_inspector/benchmarks/"]),
            Control("NIST.ME-3.1", "Independent assessments performed",         _CTL.NEAR,    []),
            Control("NIST.ME-4.1", "Trustworthy characteristics measured",      _CTL.SHIP,    ["verifier/axes.py"]),
            Control("NIST.MG-1.1", "Risk responses planned & implemented",      _CTL.SHIP,    ["incidents/", "compliance/risk_register.py"]),
            Control("NIST.MG-2.1", "Mechanisms in place to identify & track responses", _CTL.SHIP, ["compliance/risk_register.py"]),
            Control("NIST.MG-3.1", "AI risks and benefits monitored",           _CTL.SHIP,    ["observability/"]),
            Control("NIST.MG-4.1", "Post-deployment AI system monitored",       _CTL.SHIP,    ["robustness/shadow_eval.py"]),
        ],
    )


def _soc2() -> Framework:
    return Framework(
        id=FrameworkId.SOC_2, name="SOC 2 Type II — Trust Services Criteria",
        short_name="SOC 2", jurisdiction="US (AICPA)",
        summary="Trust Services Criteria across Security, Availability, Processing Integrity, Confidentiality, Privacy.",
        sla={"observation_window_days": 180},
        controls=[
            Control("SOC2.CC1.1", "Demonstrates commitment to integrity and ethics",   _CTL.PARTIAL, []),
            Control("SOC2.CC2.1", "Information about internal control communicated",   _CTL.PARTIAL, ["docs/"]),
            Control("SOC2.CC3.1", "Risk identification process",                        _CTL.SHIP,    ["compliance/risk_register.py"]),
            Control("SOC2.CC4.1", "Monitoring activities",                              _CTL.SHIP,    ["observability/", "robustness/shadow_eval.py"]),
            Control("SOC2.CC5.1", "Control activities to mitigate risks",               _CTL.SHIP,    ["verifier/", "deep_inspector/"]),
            Control("SOC2.CC6.1", "Logical access controls (RBAC)",                     _CTL.SHIP,    ["auth/rbac.py"]),
            Control("SOC2.CC6.2", "Provision and remove access timely",                  _CTL.SHIP,    ["auth/users.py", "auth/api_keys.py"]),
            Control("SOC2.CC6.3", "Least privilege",                                     _CTL.SHIP,    ["auth/rbac.py"]),
            Control("SOC2.CC6.6", "Logical access for systems",                          _CTL.SHIP,    ["auth/dependencies.py"]),
            Control("SOC2.CC6.7", "Restrict transmission of information",                _CTL.PARTIAL, ["docs/OPERATIONS.md"]),
            Control("SOC2.CC6.8", "Detect & prevent unauthorized software",              _CTL.SHIP,    ["certificate/signer.py"]),
            Control("SOC2.CC7.1", "Vulnerability detection",                             _CTL.NEAR,    []),
            Control("SOC2.CC7.2", "Monitor system performance",                           _CTL.SHIP,    ["observability/"]),
            Control("SOC2.CC7.3", "Evaluate security events",                             _CTL.SHIP,    ["incidents/__init__.py"]),
            Control("SOC2.CC7.4", "Respond to security incidents",                        _CTL.SHIP,    ["compliance/breach.py"]),
            Control("SOC2.CC7.5", "Identify & resolve incidents — RCA",                   _CTL.SHIP,    ["incidents/__init__.py"]),
            Control("SOC2.CC8.1", "Manage changes",                                       _CTL.SHIP,    ["compliance/audit_log.py"]),
            Control("SOC2.A1.1",  "Maintain availability commitments (SLA)",              _CTL.SHIP,    ["robustness/circuit_breaker.py", "tenancy/budget.py"]),
            Control("SOC2.A1.2",  "Backup & recovery",                                    _CTL.NEAR,    []),
            Control("SOC2.PI1.1", "Processing integrity (Ed25519 signed certs)",          _CTL.SHIP,    ["certificate/signer.py"]),
            Control("SOC2.C1.1",  "Confidentiality of data in process",                   _CTL.PARTIAL, ["auth/"]),
        ],
    )


def _dora() -> Framework:
    return Framework(
        id=FrameworkId.DORA, name="EU Digital Operational Resilience Act (DORA)",
        short_name="DORA", jurisdiction="EU financial entities",
        summary="ICT risk + operational resilience for EU financial sector.",
        sla={"major_incident_initial_hours": 4, "major_incident_intermediate_hours": 72,
             "major_incident_final_days": 30},
        controls=[
            Control("DORA.Art.5",  "ICT risk management framework",          _CTL.SHIP,    ["compliance/risk_register.py"]),
            Control("DORA.Art.9",  "Identification of ICT risks",             _CTL.SHIP,    ["compliance/risk_register.py"]),
            Control("DORA.Art.11", "Detection mechanisms",                    _CTL.SHIP,    ["incidents/", "observability/"]),
            Control("DORA.Art.12", "Response and recovery",                   _CTL.SHIP,    ["robustness/circuit_breaker.py"]),
            Control("DORA.Art.13", "Learning and evolving",                   _CTL.PARTIAL, ["incidents/"]),
            Control("DORA.Art.14", "Communication",                           _CTL.SHIP,    ["compliance/breach.py"]),
            Control("DORA.Art.17", "ICT-related incident management",         _CTL.SHIP,    ["incidents/", "compliance/breach.py"]),
            Control("DORA.Art.18", "Classification of incidents",             _CTL.SHIP,    ["compliance/breach.py::classify"]),
            Control("DORA.Art.19", "Reporting of major incidents",            _CTL.SHIP,    ["compliance/breach.py"]),
            Control("DORA.Art.24", "Operational resilience testing programme", _CTL.NEAR,   []),
            Control("DORA.Art.28", "ICT third-party risk management",          _CTL.NEAR,   ["integrations/"]),
        ],
    )


def _colorado_ai() -> Framework:
    return Framework(
        id=FrameworkId.COLORADO_AI, name="Colorado AI Act (SB 24-205, 2026)",
        short_name="Colorado AI", jurisdiction="US-Colorado",
        summary="Algorithmic discrimination + impact assessment for high-risk AI systems.",
        sla={"impact_assessment_review_days": 365},
        controls=[
            Control("COAI.6-1-1701", "High-risk AI system identification",        _CTL.SHIP, ["compliance/risk_register.py::is_high_risk"]),
            Control("COAI.6-1-1702", "Reasonable care to avoid algorithmic discrimination", _CTL.SHIP, ["verifier/sycophancy.py", "verifier/axes.py"]),
            Control("COAI.6-1-1703", "Algorithmic Impact Assessment (AIA)",        _CTL.SHIP, ["compliance/risk_register.py::AIIAReport"]),
            Control("COAI.6-1-1704", "Notice to consumers",                        _CTL.SHIP, ["compliance/transparency.py"]),
            Control("COAI.6-1-1705", "Right to opt-out / human review",            _CTL.PARTIAL, ["verifier/router.py"]),
            Control("COAI.6-1-1706", "Disclosure to Attorney General",              _CTL.SHIP, ["compliance/breach.py"]),
        ],
    )


def _india_dpdp() -> Framework:
    return Framework(
        id=FrameworkId.INDIA_DPDP, name="India Digital Personal Data Protection Act 2023 + IT Amendment Rules 2026",
        short_name="India DPDP", jurisdiction="India",
        summary="Indian personal data protection law + AI/IT amendment rules.",
        sla={"dsar_response_days": 30, "breach_notification_hours": 72},
        controls=[
            Control("DPDP.Sec.4",  "Lawful processing",                _CTL.SHIP, ["compliance/profiles.py"]),
            Control("DPDP.Sec.5",  "Notice to Data Principal",          _CTL.SHIP, ["compliance/transparency.py"]),
            Control("DPDP.Sec.6",  "Consent",                            _CTL.SHIP, ["compliance/consent.py"]),
            Control("DPDP.Sec.8",  "General obligations of Data Fiduciary", _CTL.SHIP, ["compliance/profiles.py"]),
            Control("DPDP.Sec.10", "Significant Data Fiduciary",          _CTL.SHIP, ["compliance/profiles.py"]),
            Control("DPDP.Sec.11", "Right to information",                _CTL.SHIP, ["compliance/dsar.py"]),
            Control("DPDP.Sec.12", "Right to correction & erasure",       _CTL.SHIP, ["compliance/dsar.py"]),
            Control("DPDP.Sec.13", "Right of grievance redressal",        _CTL.SHIP, ["compliance/dsar.py"]),
        ],
    )


def _china_genai() -> Framework:
    return Framework(
        id=FrameworkId.CHINA_GENAI, name="China Interim Measures for Generative AI Services",
        short_name="China GenAI", jurisdiction="China (CAC)",
        summary="Generative AI service rules including labeling + security review.",
        sla={"security_assessment_review_days": 365},
        controls=[
            Control("CN.GenAI.Art.4", "Adherence to socialist core values",       _CTL.PARTIAL, []),
            Control("CN.GenAI.Art.7", "Training data lawfulness",                 _CTL.PARTIAL, ["kb/versioning.py"]),
            Control("CN.GenAI.Art.8", "Data labeling guidelines",                  _CTL.PARTIAL, ["kb/versioning.py"]),
            Control("CN.GenAI.Art.9", "Algorithm registration",                    _CTL.NEAR,    []),
            Control("CN.GenAI.Art.10","User registration via real identity",       _CTL.SHIP,    ["auth/users.py"]),
            Control("CN.GenAI.Art.12","Identification of AI-generated content (label/watermark)", _CTL.NEAR, [], "Watermark NEAR-term"),
            Control("CN.GenAI.Art.14","Reporting illegal content",                  _CTL.SHIP,    ["compliance/breach.py"]),
            Control("CN.GenAI.Art.15","Take action on illegal content",            _CTL.SHIP,    ["verifier/router.py"]),
        ],
    )


def _korea_ai() -> Framework:
    return Framework(
        id=FrameworkId.KOREA_AI, name="South Korea Framework Act on AI (2026)",
        short_name="Korea AI", jurisdiction="South Korea",
        summary="Risk-based AI rules including transparency and human oversight.",
        sla={"high_risk_review_days": 180},
        controls=[
            Control("KR.AI.Art.13", "AI system risk assessment",       _CTL.SHIP,    ["compliance/risk_register.py"]),
            Control("KR.AI.Art.14", "Transparency obligations",        _CTL.SHIP,    ["compliance/transparency.py"]),
            Control("KR.AI.Art.15", "Human oversight",                  _CTL.PARTIAL, ["verifier/router.py"]),
            Control("KR.AI.Art.16", "Explainability",                   _CTL.SHIP,    ["certificate/schema.py", "compliance/model_cards.py"]),
            Control("KR.AI.Art.17", "Incident reporting",               _CTL.SHIP,    ["compliance/breach.py"]),
        ],
    )


# ---------------------------------------------------------------------------

def all_frameworks() -> list[Framework]:
    return [
        _gdpr(), _ccpa(), _iso27001(), _iso27701(), _iso42001(),
        _eu_ai_act(), _nist_ai_rmf(), _soc2(), _dora(), _colorado_ai(),
        _india_dpdp(), _china_genai(), _korea_ai(),
    ]


def get_framework(fid: FrameworkId) -> Optional[Framework]:
    for f in all_frameworks():
        if f.id == fid:
            return f
    return None


def overall_status() -> dict:
    """Compute aggregate compliance score across all frameworks."""
    frameworks = all_frameworks()
    summaries = {f.id.value: f.status_summary() for f in frameworks}
    scores = [s["score"] for s in summaries.values()]
    return {
        "compliance_version": COMPLIANCE_VERSION,
        "n_frameworks": len(frameworks),
        "mean_score": round(sum(scores) / max(len(scores), 1), 3),
        "frameworks": summaries,
    }
