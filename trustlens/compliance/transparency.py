"""Transparency artifact generators (GDPR Art.30 RoPA, EU AI Act Art.13/26,
Colorado AIA notice, Korea AI Art.14, China GenAI labeling).

These functions read the in-memory compliance state and emit
human/auditor-readable artifacts. They are pure: no I/O, no mutation —
the routes layer turns them into JSON / Markdown downloads.
"""

from __future__ import annotations

import time
from typing import Optional

from trustlens.compliance.consent import ConsentStore
from trustlens.compliance.dsar import DSARStore
from trustlens.compliance.frameworks import FrameworkId, all_frameworks, get_framework
from trustlens.compliance.model_cards import ModelCardStore
from trustlens.compliance.profiles import ProfileStore, TenantComplianceProfile
from trustlens.compliance.retention import RetentionStore
from trustlens.compliance.risk_register import RiskStore


def generate_ropa(profile: TenantComplianceProfile,
                  retention: Optional[RetentionStore] = None) -> dict:
    """GDPR Art.30 Records of Processing Activities."""
    retention_policies = []
    if retention is not None:
        retention_policies = [p.to_dict() for p in retention.all(profile.tenant_id)]
    return {
        "tenant_id": profile.tenant_id,
        "controller": {
            "legal_name": profile.legal_name,
            "address": profile.address,
            "dpo_contact": profile.dpo_contact,
            "eu_representative": profile.representative_eu,
        },
        "purposes_of_processing": profile.purposes_of_processing,
        "lawful_basis": profile.lawful_basis,
        "categories_of_data": profile.categories_of_data,
        "categories_of_subjects": profile.categories_of_subjects,
        "recipients": profile.sub_processors,
        "third_country_transfers": {
            "geographies": profile.deployment_geographies,
            "transfer_basis": profile.cross_border_basis,
        },
        "retention_policies": retention_policies,
        "security_measures": [
            "Ed25519-signed certificates",
            "RBAC + session + API-key auth",
            "PBKDF2-HMAC-SHA256 password hashing",
            "TLS at ingress",
            "Tamper-evident audit log (SHA-256 chain)",
            "Per-tenant budgets + circuit breakers",
        ],
        "generated_at": time.time(),
    }


def generate_privacy_notice(profile: TenantComplianceProfile) -> str:
    """Markdown privacy notice draft (GDPR Art.13/14, CCPA 1798.130)."""
    return (
        f"# Privacy Notice\n\n"
        f"_For service operated by **{profile.legal_name or '(legal_name TBD)'}**._\n\n"
        f"## What we collect\n"
        f"- Categories of personal data: " + ", ".join(profile.categories_of_data or ["(TBD)"]) + "\n"
        "- Categories of data subjects: " + ", ".join(profile.categories_of_subjects or ["(TBD)"]) + "\n\n"
        "## Why we process it\n"
        "- Purposes: " + ", ".join(profile.purposes_of_processing or ["service delivery"]) + "\n"
        f"- Lawful basis: **{profile.lawful_basis}**\n\n"
        f"## How long we keep it\n"
        f"- Retention is governed per data class; see RoPA.\n\n"
        f"## Your rights\n"
        f"You can exercise the following at any time by contacting "
        f"**{profile.dpo_contact or '(dpo@TBD)'}**:\n"
        f"- Right of access (GDPR Art.15 / CCPA right to know)\n"
        f"- Right to rectification (GDPR Art.16 / CCPA right to correct)\n"
        f"- Right to erasure (GDPR Art.17 / CCPA right to delete)\n"
        f"- Right to data portability (GDPR Art.20)\n"
        f"- Right to restrict / object to processing (GDPR Art.18, 21)\n"
        f"- Right to opt-out of sale or sharing (CCPA 1798.120)\n\n"
        f"## Cross-border transfers\n"
        f"- Geographies: " + ", ".join(profile.deployment_geographies or ["(TBD)"]) + "\n"
        "- Transfer basis: " + ", ".join(profile.cross_border_basis or ["SCC / adequacy"]) + "\n\n"
        "## Automated decision-making (AI)\n"
        "This service uses TrustLens to verify AI outputs. Every response is\n"
        "accompanied by a signed certificate stating which claims are grounded\n"
        "and which are flagged. You may request human review of any AI-assisted\n"
        "decision via the contact above (GDPR Art.22).\n"
    )


def generate_eu_ai_act_summary(profile: TenantComplianceProfile,
                                model_cards: ModelCardStore,
                                risks: RiskStore) -> dict:
    """EU AI Act Art.13/26 deployer/provider information packet."""
    return {
        "tenant_id": profile.tenant_id,
        "risk_classification": "high" if profile.is_high_risk_ai else "limited",
        "intended_purposes": profile.purposes_of_processing,
        "model_cards": [c.to_dict() for c in model_cards.all(profile.tenant_id)],
        "risks": [r.to_dict() for r in risks.all(profile.tenant_id)],
        "human_oversight": "Operators may set per-tenant `tier=DEEP` and "
                           "review claim-level verdicts before delivery. "
                           "DEEP tier surfaces SSH/steering evidence in the cert.",
        "monitoring": "Post-market monitoring via Prometheus metrics + "
                      "shadow-eval sampler (robustness/shadow_eval.py).",
        "incident_reporting": "Serious incidents recorded via "
                              "compliance/breach.py with a 15-day window.",
        "generated_at": time.time(),
    }


def generate_compliance_overview(profile: Optional[TenantComplianceProfile]) -> dict:
    """The big red/yellow/green grid the COMPLIANCE OVERVIEW dashboard renders."""
    fids = (profile.applicable_frameworks if profile and profile.applicable_frameworks
            else [f.id.value for f in all_frameworks()])
    out: list[dict] = []
    for fid in fids:
        try:
            fw = get_framework(FrameworkId(fid))
        except ValueError:
            continue
        if fw is None:
            continue
        s = fw.status_summary()
        out.append({
            "id": fw.id.value, "name": fw.name, "short": fw.short_name,
            "jurisdiction": fw.jurisdiction, "score": s["score"],
            "ship": s["ship"], "partial": s["partial"],
            "near": s["near"], "gap": s["gap"], "total": s["total"],
        })
    return {"frameworks": out, "ts": time.time()}


def generate_dsar_summary(dsar: DSARStore,
                           tenant_id: Optional[str] = None) -> dict:
    items = dsar.all(tenant_id=tenant_id)
    open_n = sum(1 for r in items if r.status not in ("fulfilled", "rejected"))
    overdue_n = sum(1 for r in items if r.is_overdue())
    return {"total": len(items), "open": open_n, "overdue": overdue_n}


def generate_consent_summary(consent: ConsentStore,
                              tenant_id: str) -> dict:
    rs = consent.all_for_tenant(tenant_id)
    by_purpose: dict[str, dict] = {}
    for r in rs:
        d = by_purpose.setdefault(r.purpose, {"granted": 0, "withdrawn": 0, "expired": 0})
        if r.status in d: d[r.status] += 1
    return {"total": len(rs), "by_purpose": by_purpose}
