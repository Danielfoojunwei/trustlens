"""Admin REST endpoints for the compliance subsystem.

Mounted at /v1/admin/compliance/* — all permission-gated via the existing
``require_permission`` dependency. The COMPLIANCE dashboard pulls its data
from these endpoints.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from trustlens.auth.dependencies import require_permission
from trustlens.auth.rbac import Permission
from trustlens.auth.users import User
from trustlens.compliance import (
    AIIAReport, BreachStore, ConsentStore, DSARStore, FrameworkId,
    InMemoryAuditLog, ModelCard, ModelCardStore, ProfileStore,
    RetentionPolicy, RetentionStore, RiskItem, RiskStore,
    TenantComplianceProfile, all_frameworks, classify, default_policies,
    export_csv, export_jsonl, generate_compliance_overview,
    generate_consent_summary, generate_dsar_summary,
    generate_eu_ai_act_summary, generate_privacy_notice, generate_ropa,
    get_framework, is_high_risk_eu_ai_act, overall_status, risk_score,
    seed_default_risks, starter_profile,
)
from trustlens.compliance.audit_log import AuditLogStore


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class ConsentBody(BaseModel):
    tenant_id: str
    data_subject_id: str
    purpose: str
    status: str
    lawful_basis: Optional[str] = None
    expires_at: Optional[float] = None
    captured_via: Optional[str] = None
    evidence_uri: Optional[str] = None


class DSARBody(BaseModel):
    tenant_id: str
    data_subject_id: str
    type: str
    jurisdiction: str = "gdpr"
    received_via: Optional[str] = None
    contact: Optional[str] = None


class DSARUpdate(BaseModel):
    status: Optional[str] = None
    note: Optional[str] = None
    fulfilled_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    artifact_uri: Optional[str] = None


class RetentionBody(BaseModel):
    tenant_id: str
    data_class: str
    retention_days: int
    deletion_method: str = "purge"
    legal_hold: bool = False
    notes: str = ""


class BreachBody(BaseModel):
    tenant_id: Optional[str] = None
    severity: str
    kind: str
    title: str
    summary: str
    jurisdictions: list[str]
    data_classes: list[str] = []
    affected_subjects_estimate: Optional[int] = None
    metadata: Optional[dict] = None


class BreachNotifyBody(BaseModel):
    window_id: str


class BreachCloseBody(BaseModel):
    rcca_uri: Optional[str] = None


class RiskBody(BaseModel):
    tenant_id: Optional[str] = None
    category: str
    title: str
    description: str = ""
    likelihood: str = "possible"
    impact: str = "moderate"
    controls: list[str] = []
    framework_refs: list[str] = []
    owner: Optional[str] = None


class RiskUpdate(BaseModel):
    likelihood: Optional[str] = None
    impact: Optional[str] = None
    status: Optional[str] = None
    controls: Optional[list[str]] = None
    owner: Optional[str] = None
    notes: Optional[str] = None


class AIIABody(BaseModel):
    tenant_id: Optional[str] = None
    system_name: str
    intended_purpose: str
    affected_groups: list[str] = []
    deployed_geographies: list[str] = []
    risks: list[str] = []
    mitigations: list[str] = []
    human_oversight_summary: str = ""
    monitoring_summary: str = ""
    framework_refs: list[str] = []


class ModelCardBody(BaseModel):
    tenant_id: Optional[str] = None
    system_name: str
    provider: str
    model_id: str
    intended_use: str
    out_of_scope_use: list[str] = []
    user_groups: list[str] = []
    deployment_geographies: list[str] = []
    training_data_summary: str = ""
    evaluation_data_summary: str = ""
    performance_metrics: dict = {}
    risks: list[str] = []
    mitigations: list[str] = []
    human_oversight: str = ""
    monitoring_plan: str = ""
    contact: str = ""
    framework_refs: list[str] = []


class ModelCardUpdate(BaseModel):
    summary: str = "edit"
    intended_use: Optional[str] = None
    out_of_scope_use: Optional[list[str]] = None
    deployment_geographies: Optional[list[str]] = None
    training_data_summary: Optional[str] = None
    performance_metrics: Optional[dict] = None
    risks: Optional[list[str]] = None
    mitigations: Optional[list[str]] = None
    human_oversight: Optional[str] = None
    monitoring_plan: Optional[str] = None
    contact: Optional[str] = None


class ProfileBody(BaseModel):
    legal_name: Optional[str] = None
    address: Optional[str] = None
    dpo_contact: Optional[str] = None
    representative_eu: Optional[str] = None
    lawful_basis: Optional[str] = None
    purposes_of_processing: Optional[list[str]] = None
    categories_of_data: Optional[list[str]] = None
    categories_of_subjects: Optional[list[str]] = None
    deployment_geographies: Optional[list[str]] = None
    data_residency: Optional[list[str]] = None
    cross_border_basis: Optional[list[str]] = None
    sub_processors: Optional[list[str]] = None
    applicable_frameworks: Optional[list[str]] = None
    breach_reporting_jurisdictions: Optional[list[str]] = None
    sensitive_pi: Optional[bool] = None
    is_significant_data_fiduciary: Optional[bool] = None
    is_high_risk_ai: Optional[bool] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def build_compliance_router(
    *,
    audit_log: AuditLogStore,
    consent: ConsentStore,
    dsar: DSARStore,
    retention: RetentionStore,
    breach: BreachStore,
    risk: RiskStore,
    model_cards: ModelCardStore,
    profiles: ProfileStore,
) -> APIRouter:
    router = APIRouter(prefix="/v1/admin/compliance", tags=["compliance"])

    # -------------------------------------------------------- frameworks
    @router.get("/frameworks")
    async def list_frameworks(
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> list[dict]:
        out = []
        for f in all_frameworks():
            d = {
                "id": f.id.value, "name": f.name, "short_name": f.short_name,
                "jurisdiction": f.jurisdiction, "summary": f.summary,
                "sla": f.sla, "status": f.status_summary(),
            }
            out.append(d)
        return out

    @router.get("/frameworks/{fid}")
    async def get_one(
        fid: str,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> dict:
        try:
            fw = get_framework(FrameworkId(fid))
        except ValueError:
            raise HTTPException(status_code=404, detail="unknown_framework")
        if fw is None:
            raise HTTPException(status_code=404, detail="not_loaded")
        return {
            "id": fw.id.value, "name": fw.name, "jurisdiction": fw.jurisdiction,
            "summary": fw.summary, "sla": fw.sla,
            "status": fw.status_summary(),
            "controls": [{
                "id": c.control_id, "title": c.title,
                "status": c.status.value, "evidence": c.evidence,
                "notes": c.notes,
            } for c in fw.controls],
        }

    @router.get("/overview")
    async def overview(
        tenant_id: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> dict:
        prof = profiles.get(tenant_id) if tenant_id else None
        return {
            "global": overall_status(),
            "by_tenant": generate_compliance_overview(prof),
        }

    # ---------------------------------------------------------- audit log
    @router.get("/audit-log")
    async def list_audit(
        limit: int = Query(default=200, ge=1, le=5000),
        tenant_id: Optional[str] = None,
        action_prefix: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> list[dict]:
        return [e.to_dict() for e in audit_log.all(
            limit=limit, tenant_id=tenant_id, action_prefix=action_prefix,
        )]

    @router.get("/audit-log/verify")
    async def verify_audit(
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> dict:
        r = audit_log.verify()
        return {"ok": r.ok, "n_events": r.n_events,
                "first_break_seq": r.first_break_seq, "reason": r.reason}

    @router.get("/audit-log/export")
    async def export_audit(
        fmt: str = "jsonl",
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> PlainTextResponse:
        events = audit_log.all()
        if fmt == "csv":
            return PlainTextResponse(
                export_csv(events), media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
            )
        return PlainTextResponse(
            export_jsonl(events), media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="audit-log.jsonl"'},
        )

    # ---------------------------------------------------------- consent
    @router.post("/consent")
    async def post_consent(
        body: ConsentBody,
        user: User = Depends(require_permission(Permission.INTEGRATIONS_WRITE)),
    ) -> dict:
        r = consent.record(
            tenant_id=body.tenant_id,
            data_subject_id=body.data_subject_id,
            purpose=body.purpose, status=body.status,
            lawful_basis=body.lawful_basis, expires_at=body.expires_at,
            captured_via=body.captured_via or "ui",
            evidence_uri=body.evidence_uri,
        )
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="consent.record", outcome="success",
                          tenant_id=body.tenant_id,
                          resource=f"subject:{body.data_subject_id}/purpose:{body.purpose}",
                          metadata={"status": body.status})
        return r.to_dict()

    @router.get("/consent")
    async def list_consent(
        tenant_id: str,
        limit: int = 200,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> list[dict]:
        return [r.to_dict() for r in consent.all_for_tenant(tenant_id, limit)]

    @router.get("/consent/summary")
    async def consent_summary(
        tenant_id: str,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> dict:
        return generate_consent_summary(consent, tenant_id)

    # ---------------------------------------------------------- DSAR
    @router.post("/dsar")
    async def open_dsar(
        body: DSARBody,
        user: User = Depends(require_permission(Permission.VIEW_INCIDENTS)),
    ) -> dict:
        r = dsar.open(
            tenant_id=body.tenant_id, data_subject_id=body.data_subject_id,
            type=body.type, jurisdiction=body.jurisdiction,
            received_via=body.received_via, contact=body.contact,
        )
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="dsar.open", outcome="success",
                          tenant_id=body.tenant_id,
                          resource=f"dsar:{r.request_id}",
                          metadata={"type": body.type,
                                    "jurisdiction": body.jurisdiction})
        return r.to_dict()

    @router.get("/dsar")
    async def list_dsar(
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_INCIDENTS)),
    ) -> list[dict]:
        return [r.to_dict() for r in dsar.all(tenant_id=tenant_id,
                                                status=status)]

    @router.patch("/dsar/{request_id}")
    async def update_dsar(
        request_id: str, body: DSARUpdate,
        user: User = Depends(require_permission(Permission.INCIDENTS_ACK)),
    ) -> dict:
        r = dsar.update(request_id, status=body.status, note=body.note,
                          fulfilled_by=body.fulfilled_by or user.email,
                          rejection_reason=body.rejection_reason,
                          artifact_uri=body.artifact_uri)
        if r is None:
            raise HTTPException(status_code=404, detail="not_found")
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="dsar.update", outcome="success",
                          tenant_id=r.tenant_id,
                          resource=f"dsar:{request_id}",
                          metadata={"status": body.status})
        return r.to_dict()

    @router.get("/dsar/summary")
    async def dsar_sum(
        tenant_id: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_INCIDENTS)),
    ) -> dict:
        return generate_dsar_summary(dsar, tenant_id)

    # -------------------------------------------------------- retention
    @router.get("/retention")
    async def list_retention(
        tenant_id: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> list[dict]:
        return [p.to_dict() for p in retention.all(tenant_id)]

    @router.put("/retention")
    async def upsert_retention(
        body: RetentionBody,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        p = RetentionPolicy(
            tenant_id=body.tenant_id, data_class=body.data_class,
            retention_days=body.retention_days,
            deletion_method=body.deletion_method,
            legal_hold=body.legal_hold, notes=body.notes,
            updated_by=user.email,
        )
        retention.set(p)
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="retention.set", outcome="success",
                          tenant_id=body.tenant_id,
                          resource=f"retention:{body.data_class}",
                          metadata={"retention_days": body.retention_days,
                                    "deletion_method": body.deletion_method,
                                    "legal_hold": body.legal_hold})
        return p.to_dict()

    @router.delete("/retention/{tenant_id}/{data_class}")
    async def del_retention(
        tenant_id: str, data_class: str,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        ok = retention.delete(tenant_id, data_class)
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="retention.delete", outcome=("success" if ok else "failure"),
                          tenant_id=tenant_id, resource=f"retention:{data_class}")
        return {"ok": ok}

    @router.post("/retention/seed")
    async def seed_retention(
        tenant_id: str,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> list[dict]:
        out = []
        for p in default_policies(tenant_id):
            p.updated_by = user.email
            retention.set(p)
            out.append(p.to_dict())
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="retention.seed", outcome="success",
                          tenant_id=tenant_id, metadata={"count": len(out)})
        return out

    # ---------------------------------------------------------- breach
    @router.post("/breach")
    async def open_breach(
        body: BreachBody,
        user: User = Depends(require_permission(Permission.INCIDENTS_WEBHOOK)),
    ) -> dict:
        r = breach.open(
            tenant_id=body.tenant_id, severity=body.severity,
            kind=body.kind, title=body.title, summary=body.summary,
            jurisdictions=body.jurisdictions,
            data_classes=body.data_classes,
            affected_subjects_estimate=body.affected_subjects_estimate,
            metadata=body.metadata,
        )
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="breach.open", outcome="success",
                          tenant_id=body.tenant_id,
                          resource=f"breach:{r.breach_id}",
                          metadata={"severity": body.severity,
                                    "classification": classify(
                                        body.severity,
                                        body.affected_subjects_estimate)})
        return r.to_dict()

    @router.get("/breach")
    async def list_breach(
        tenant_id: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_INCIDENTS)),
    ) -> list[dict]:
        return [r.to_dict() for r in breach.all(tenant_id)]

    @router.get("/breach/overdue")
    async def overdue_breach(
        _: User = Depends(require_permission(Permission.VIEW_INCIDENTS)),
    ) -> list[dict]:
        return [{"breach": b.to_dict(), "window_id": w,
                 "hours_overdue": round(h, 2)}
                for b, w, h in breach.overdue()]

    @router.post("/breach/{breach_id}/notify")
    async def breach_notified(
        breach_id: str, body: BreachNotifyBody,
        user: User = Depends(require_permission(Permission.INCIDENTS_WEBHOOK)),
    ) -> dict:
        r = breach.mark_notified(breach_id, body.window_id)
        if r is None:
            raise HTTPException(status_code=404, detail="not_found")
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="breach.notify", outcome="success",
                          tenant_id=r.tenant_id,
                          resource=f"breach:{breach_id}",
                          metadata={"window": body.window_id})
        return r.to_dict()

    @router.post("/breach/{breach_id}/close")
    async def close_breach(
        breach_id: str, body: BreachCloseBody,
        user: User = Depends(require_permission(Permission.INCIDENTS_WEBHOOK)),
    ) -> dict:
        r = breach.close(breach_id, rcca_uri=body.rcca_uri)
        if r is None:
            raise HTTPException(status_code=404, detail="not_found")
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="breach.close", outcome="success",
                          tenant_id=r.tenant_id,
                          resource=f"breach:{breach_id}")
        return r.to_dict()

    # --------------------------------------------------------- risks
    @router.get("/risks")
    async def list_risks(
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> list[dict]:
        return [r.to_dict() for r in risk.all(tenant_id, status)]

    @router.post("/risks")
    async def add_risk(
        body: RiskBody,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        import secrets
        s = risk_score(body.likelihood, body.impact)
        r = RiskItem(
            risk_id="risk_" + secrets.token_hex(5),
            tenant_id=body.tenant_id, category=body.category,
            title=body.title, description=body.description,
            likelihood=body.likelihood, impact=body.impact,
            score=s, inherent_score=s, residual_score=s,
            controls=body.controls, framework_refs=body.framework_refs,
            owner=body.owner,
        )
        risk.add(r)
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="risk.add", outcome="success",
                          tenant_id=body.tenant_id,
                          resource=f"risk:{r.risk_id}")
        return r.to_dict()

    @router.patch("/risks/{risk_id}")
    async def patch_risk(
        risk_id: str, body: RiskUpdate,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        r = risk.update(risk_id, **body.model_dump(exclude_unset=True))
        if r is None:
            raise HTTPException(status_code=404, detail="not_found")
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="risk.update", outcome="success",
                          resource=f"risk:{risk_id}")
        return r.to_dict()

    @router.post("/risks/seed")
    async def seed_risks(
        tenant_id: Optional[str] = None,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> list[dict]:
        out = [risk.add(r).to_dict() for r in seed_default_risks(tenant_id)]
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="risk.seed", outcome="success",
                          tenant_id=tenant_id, metadata={"count": len(out)})
        return out

    # --------------------------------------------------------- AIIA
    @router.post("/aiia")
    async def add_aiia(
        body: AIIABody,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        import secrets, time as _t
        cls = "high" if is_high_risk_eu_ai_act(
            body.intended_purpose, body.deployed_geographies,
        ) else "limited"
        report = AIIAReport(
            report_id="aiia_" + secrets.token_hex(5),
            tenant_id=body.tenant_id,
            system_name=body.system_name,
            intended_purpose=body.intended_purpose,
            risk_classification=cls,
            affected_groups=body.affected_groups,
            deployed_geographies=body.deployed_geographies,
            risks=body.risks, mitigations=body.mitigations,
            human_oversight_summary=body.human_oversight_summary,
            monitoring_summary=body.monitoring_summary,
            sign_off_by=user.email, sign_off_at=_t.time(),
            next_review_at=_t.time() + 365 * 86_400.0,
            framework_refs=body.framework_refs,
        )
        risk.add_aiia(report)
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="aiia.create", outcome="success",
                          tenant_id=body.tenant_id,
                          resource=f"aiia:{report.report_id}",
                          metadata={"risk_classification": cls})
        return report.to_dict()

    @router.get("/aiia")
    async def list_aiia(
        tenant_id: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> list[dict]:
        return [a.to_dict() for a in risk.aiias(tenant_id)]

    # ---------------------------------------------------- model cards
    @router.get("/model-cards")
    async def list_cards(
        tenant_id: Optional[str] = None,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> list[dict]:
        return [c.to_dict() for c in model_cards.all(tenant_id)]

    @router.post("/model-cards")
    async def create_card(
        body: ModelCardBody,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        card = ModelCard(
            card_id="", tenant_id=body.tenant_id,
            system_name=body.system_name, provider=body.provider,
            model_id=body.model_id, intended_use=body.intended_use,
            out_of_scope_use=body.out_of_scope_use,
            user_groups=body.user_groups,
            deployment_geographies=body.deployment_geographies,
            training_data_summary=body.training_data_summary,
            evaluation_data_summary=body.evaluation_data_summary,
            performance_metrics=body.performance_metrics,
            risks=body.risks, mitigations=body.mitigations,
            human_oversight=body.human_oversight,
            monitoring_plan=body.monitoring_plan,
            contact=body.contact, framework_refs=body.framework_refs,
        )
        c = model_cards.create(card, committed_by=user.email)
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="modelcard.create", outcome="success",
                          tenant_id=body.tenant_id,
                          resource=f"modelcard:{c.card_id}")
        return c.to_dict()

    @router.patch("/model-cards/{card_id}")
    async def update_card(
        card_id: str, body: ModelCardUpdate,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        c = model_cards.update(card_id, committed_by=user.email,
                                **body.model_dump(exclude={"summary"},
                                                  exclude_unset=True),
                                summary=body.summary)
        if c is None:
            raise HTTPException(status_code=404, detail="not_found")
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="modelcard.update", outcome="success",
                          resource=f"modelcard:{card_id}",
                          metadata={"version": c.current_version})
        return c.to_dict()

    @router.delete("/model-cards/{card_id}")
    async def delete_card(
        card_id: str,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        ok = model_cards.delete(card_id)
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="modelcard.delete",
                          outcome=("success" if ok else "failure"),
                          resource=f"modelcard:{card_id}")
        return {"ok": ok}

    # ----------------------------------------------------- profile
    @router.get("/profile/{tenant_id}")
    async def get_profile(
        tenant_id: str,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> dict:
        p = profiles.get(tenant_id) or starter_profile(tenant_id)
        return p.to_dict()

    @router.put("/profile/{tenant_id}")
    async def put_profile(
        tenant_id: str, body: ProfileBody,
        user: User = Depends(require_permission(Permission.VERIFIER_SETTINGS)),
    ) -> dict:
        p = profiles.get(tenant_id) or starter_profile(tenant_id)
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(p, k, v)
        p.updated_by = user.email
        profiles.upsert(p)
        audit_log.append(actor=user.email, actor_role=user.role.value,
                          action="profile.update", outcome="success",
                          tenant_id=tenant_id)
        return p.to_dict()

    # ------------------------------------------------- transparency
    @router.get("/transparency/{tenant_id}/ropa")
    async def ropa(
        tenant_id: str,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> dict:
        prof = profiles.get(tenant_id) or starter_profile(tenant_id)
        return generate_ropa(prof, retention)

    @router.get("/transparency/{tenant_id}/privacy-notice")
    async def privacy_notice(
        tenant_id: str,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> PlainTextResponse:
        prof = profiles.get(tenant_id) or starter_profile(tenant_id)
        return PlainTextResponse(
            generate_privacy_notice(prof),
            media_type="text/markdown",
            headers={"Content-Disposition":
                     f'attachment; filename="privacy-notice-{tenant_id}.md"'},
        )

    @router.get("/transparency/{tenant_id}/eu-ai-act")
    async def eu_ai_act_pack(
        tenant_id: str,
        _: User = Depends(require_permission(Permission.VIEW_OVERVIEW)),
    ) -> dict:
        prof = profiles.get(tenant_id) or starter_profile(tenant_id)
        return generate_eu_ai_act_summary(prof, model_cards, risk)

    return router
