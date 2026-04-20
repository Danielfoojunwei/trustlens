"""TrustLens MCP server.

Surfaces the entire TrustLens control plane as ~40 agent-callable tools
plus a small set of resources (compliance overview, framework catalog).

Design rules:
    1. Every mutating tool documents the impact in its docstring (the
       agent sees this and can decide whether to ask the user first).
    2. Tools return structured JSON-able dicts with a ``status`` field.
    3. Tools that need missing inputs return ``status: "needs_input"``
       with a ``required`` list — the agent uses this to drive a
       clarifying conversation with the user.
    4. The server can run against a remote gateway (default) OR boot an
       embedded gateway (``--embedded``) for fully self-contained dev.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP   # type: ignore[import-not-found]

from trustlens.mcp.client import GatewayClient
from trustlens.version import __version__, PIPELINE_VERSION


TRUSTLENS_TOOL_VERSION = "trustlens.mcp/1.0.0"


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def build_server(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> FastMCP:
    mcp = FastMCP(
        "trustlens",
        instructions=(
            "TrustLens is a verifiable hallucination-control layer for any LLM. "
            "Use these tools to install, operate, audit, and tune it on behalf "
            "of the user. ALWAYS confirm with the user before any destructive "
            "or compliance-relevant write (KB delete, breach close, retention "
            "change, signer key rotation). Read-only views are safe to call "
            "freely."
        ),
    )
    client = GatewayClient(base_url=base_url, api_key=api_key, tenant_id=tenant_id)

    # ----------------------------------------------------------------------
    # SESSION + DISCOVERY
    # ----------------------------------------------------------------------

    @mcp.tool()
    async def trustlens_version() -> dict:
        """Return TrustLens version + MCP tool version. Use this first to
        confirm you're talking to a live gateway."""
        try:
            health = await client.get("/healthz")
        except Exception as e:
            return {"status": "gateway_down", "tool_version": TRUSTLENS_TOOL_VERSION,
                    "trustlens_version": __version__,
                    "pipeline_version": PIPELINE_VERSION,
                    "base_url": client.base_url, "error": str(e)}
        return {"status": "ok",
                "tool_version": TRUSTLENS_TOOL_VERSION,
                "trustlens_version": __version__,
                "pipeline_version": PIPELINE_VERSION,
                "gateway": health, "base_url": client.base_url,
                "tenant_id": client.tenant_id}

    @mcp.tool()
    async def login(email: str, password: str) -> dict:
        """Authenticate the MCP session with a TrustLens user account.
        Required before calling any admin tool. Use ``trustlens_version``
        first to discover the gateway."""
        if not email or not password:
            return {"status": "needs_input",
                    "required": ["email", "password"],
                    "hint": "Default dev accounts: owner@trustlens.local/trustlens, "
                            "operator@trustlens.local/operator, "
                            "viewer@trustlens.local/viewer"}
        try:
            r = await client.login(email, password)
        except Exception as e:
            return {"status": "failed", "error": str(e)}
        if not r.get("ok"):
            return {"status": "failed", "reason": r.get("reason"),
                    "hint": "Default dev passwords: trustlens / operator / viewer"}
        return {"status": "ok", "user": r["user"]}

    @mcp.tool()
    async def whoami() -> dict:
        """Show the currently authenticated user + role + permissions."""
        return await client.whoami()

    @mcp.tool()
    async def list_endpoints() -> dict:
        """List every REST endpoint the gateway exposes — useful for
        discovery before constructing a custom workflow."""
        try:
            spec = await client.get("/openapi.json")
        except Exception as e:
            return {"status": "failed", "error": str(e)}
        paths = []
        for p, methods in (spec.get("paths") or {}).items():
            for m, op in methods.items():
                if m.lower() in {"get", "post", "put", "patch", "delete"}:
                    paths.append({
                        "method": m.upper(), "path": p,
                        "summary": op.get("summary") or op.get("operationId") or "",
                    })
        return {"status": "ok", "n": len(paths), "endpoints": paths}

    # ----------------------------------------------------------------------
    # CHAT + VERIFICATION
    # ----------------------------------------------------------------------

    @mcp.tool()
    async def chat(prompt: str, model: str = "echo",
                   tier: str = "standard",
                   tau: Optional[float] = None,
                   tau_prime: Optional[float] = None) -> dict:
        """Send a chat completion through the verified gateway. Returns
        the response *and* the signed certificate annotation
        (cert_id, cert_status, masked_claim_ids).

        Args:
            prompt: user message
            model: backend model id (echo / gpt-4o / claude-3-... / llama3 / ...)
            tier:  fast | standard | deep
            tau:   verdict threshold for VERIFIED (0–1, optional)
            tau_prime: contradiction threshold (0–1, optional)
        """
        if not prompt:
            return {"status": "needs_input", "required": ["prompt"]}
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "trustlens": {"verification_tier": tier},
        }
        if tau is not None:        body["trustlens"]["tau"] = tau
        if tau_prime is not None:  body["trustlens"]["tau_prime"] = tau_prime
        return await client.post("/v1/chat/completions", body)

    @mcp.tool()
    async def verify_certificate(certificate_id: str) -> dict:
        """Look up a previously issued certificate by id and return its
        signature status + per-claim verdicts."""
        if not certificate_id:
            return {"status": "needs_input", "required": ["certificate_id"]}
        return await client.get(f"/v1/admin/certs/{certificate_id}")

    # ----------------------------------------------------------------------
    # KB MANAGEMENT
    # ----------------------------------------------------------------------

    @mcp.tool()
    async def kb_list(tenant_id: str = "") -> dict:
        """List documents in a tenant's knowledge base."""
        t = tenant_id or client.tenant_id
        return {"tenant_id": t,
                "documents": await client.get(f"/v1/admin/kb/{t}/docs")}

    @mcp.tool()
    async def kb_upsert(tenant_id: str,
                        documents: list[dict]) -> dict:
        """Bulk upsert KB documents. Each doc requires ``doc_id`` and
        ``text``; ``source_uri`` and ``metadata`` are optional. Creates a
        new KB version. Confirm with the user before large overwrites."""
        if not tenant_id or not documents:
            return {"status": "needs_input", "required": ["tenant_id", "documents"]}
        return await client.post("/v1/admin/kb/upsert",
                                  {"tenant_id": tenant_id, "documents": documents})

    @mcp.tool()
    async def kb_delete(tenant_id: str, doc_ids: list[str]) -> dict:
        """Delete documents from a KB. Destructive — confirm with the user.
        Creates a new KB version that can be reverted."""
        if not tenant_id or not doc_ids:
            return {"status": "needs_input", "required": ["tenant_id", "doc_ids"],
                    "warning": "destructive — confirm with user"}
        return await client.post("/v1/admin/kb/delete",
                                  {"tenant_id": tenant_id, "doc_ids": doc_ids})

    @mcp.tool()
    async def kb_versions(tenant_id: str) -> dict:
        """List the KB version timeline for a tenant."""
        return {"tenant_id": tenant_id,
                "versions": await client.get(f"/v1/admin/kb/{tenant_id}/versions")}

    @mcp.tool()
    async def kb_revert(tenant_id: str, version: int) -> dict:
        """Revert a tenant's KB to a prior version. Destructive — confirm."""
        return await client.post("/v1/admin/kb/revert",
                                  {"tenant_id": tenant_id, "version": version})

    @mcp.tool()
    async def kb_export(tenant_id: str, fmt: str = "jsonl") -> dict:
        """Export a tenant's KB. fmt: 'jsonl' or 'csv'."""
        text = await client.get(f"/v1/admin/kb/{tenant_id}/export?fmt={fmt}")
        return {"format": fmt, "tenant_id": tenant_id, "body": text}

    # ----------------------------------------------------------------------
    # 3-AXIS / OBSERVABILITY
    # ----------------------------------------------------------------------

    @mcp.tool()
    async def axes_summary(window_s: int = 300, tenant_id: str = "") -> dict:
        """Snapshot of the 3-axis epistemic state (internal × external ×
        sycophancy) over a time window."""
        q = f"?window_s={window_s}"
        if tenant_id: q += f"&tenant_id={tenant_id}"
        return await client.get(f"/v1/admin/axes/summary{q}")

    @mcp.tool()
    async def axes_recent(limit: int = 100, since_s: int = 300,
                          tenant_id: str = "") -> dict:
        """Recent per-cert axis points for plotting/analysis."""
        q = f"?limit={limit}&since_s={since_s}"
        if tenant_id: q += f"&tenant_id={tenant_id}"
        return {"points": await client.get(f"/v1/admin/axes/recent{q}")}

    # ----------------------------------------------------------------------
    # INCIDENTS
    # ----------------------------------------------------------------------

    @mcp.tool()
    async def incidents_list(severity: str = "", kind: str = "",
                              acked: Optional[bool] = None,
                              limit: int = 100) -> dict:
        """List recent incidents (SSH critical, RAD-CoT engages, oracle
        outages, blocked-cert spikes)."""
        q = [f"limit={limit}"]
        if severity: q.append(f"severity={severity}")
        if kind:     q.append(f"kind={kind}")
        if acked is not None: q.append(f"acked={'true' if acked else 'false'}")
        return {"incidents": await client.get("/v1/admin/incidents?" + "&".join(q))}

    @mcp.tool()
    async def incident_ack(incident_id: str, note: str = "") -> dict:
        """Acknowledge an incident. The agent should explain why it's
        acking before calling this."""
        return await client.post(f"/v1/admin/incidents/{incident_id}/ack",
                                  {"note": note})

    # ----------------------------------------------------------------------
    # FEATURE SETTINGS + TIER
    # ----------------------------------------------------------------------

    @mcp.tool()
    async def settings_get() -> dict:
        """Read the current verifier feature settings (NLI toggles, SSH ρ,
        steering α, etc.)."""
        return await client.get("/v1/admin/settings")

    @mcp.tool()
    async def settings_update(
        sycophancy_enabled: Optional[bool] = None,
        negation_aware_enabled: Optional[bool] = None,
        numeric_aware_enabled: Optional[bool] = None,
        transformer_nli_enabled: Optional[bool] = None,
        deep_inspector_default: Optional[bool] = None,
        ssh_threshold_rho: Optional[float] = None,
        ssh_compute_every_n: Optional[int] = None,
        steering_alpha: Optional[float] = None,
        steering_top_k_layers: Optional[int] = None,
    ) -> dict:
        """Patch verifier feature settings. Each arg is optional; only
        provided fields are updated. ssh_threshold_rho should be in
        [0.80, 1.00]; raising it reduces SSH critical alarms."""
        body = {k: v for k, v in {
            "sycophancy_enabled": sycophancy_enabled,
            "negation_aware_enabled": negation_aware_enabled,
            "numeric_aware_enabled": numeric_aware_enabled,
            "transformer_nli_enabled": transformer_nli_enabled,
            "deep_inspector_default": deep_inspector_default,
            "ssh_threshold_rho": ssh_threshold_rho,
            "ssh_compute_every_n": ssh_compute_every_n,
            "steering_alpha": steering_alpha,
            "steering_top_k_layers": steering_top_k_layers,
        }.items() if v is not None}
        if not body:
            return {"status": "noop", "reason": "no fields supplied"}
        return await client.put("/v1/admin/settings", body)

    # ----------------------------------------------------------------------
    # USERS + API KEYS
    # ----------------------------------------------------------------------

    @mcp.tool()
    async def users_list() -> dict:
        """List operator users."""
        return {"users": await client.get("/v1/auth/users")}

    @mcp.tool()
    async def user_create(email: str, display_name: str, role: str,
                           password: Optional[str] = None) -> dict:
        """Create a new operator user. Roles: owner, admin, operator, viewer."""
        if role not in {"owner", "admin", "operator", "viewer"}:
            return {"status": "needs_input", "reason": "bad_role",
                    "valid_roles": ["owner", "admin", "operator", "viewer"]}
        return await client.post("/v1/auth/users",
                                  {"email": email, "display_name": display_name,
                                   "role": role, "password": password})

    @mcp.tool()
    async def keys_list() -> dict:
        """List API keys (secrets are not returned)."""
        return {"keys": await client.get("/v1/auth/keys")}

    @mcp.tool()
    async def key_mint(name: str, tenant_id: str, role: str) -> dict:
        """Mint a new API key. **Plaintext secret is returned ONCE** —
        store it now. Role: viewer / operator / admin."""
        return await client.post("/v1/auth/keys",
                                  {"name": name, "tenant_id": tenant_id, "role": role})

    @mcp.tool()
    async def key_revoke(key_id: str) -> dict:
        """Revoke an API key. Destructive — confirm with the user."""
        return await client.delete(f"/v1/auth/keys/{key_id}")

    # ----------------------------------------------------------------------
    # INTEGRATIONS
    # ----------------------------------------------------------------------

    @mcp.tool()
    async def integrations_list() -> dict:
        """List configured integrations (oracles, alert sinks, OIDC)."""
        return {"integrations": await client.get("/v1/admin/integrations")}

    @mcp.tool()
    async def integration_set(kind: str, enabled: bool,
                               name: str = "", config: Optional[dict] = None) -> dict:
        """Enable/disable + configure an integration. Valid kinds include:
        oracle.wikidata, oracle.customer_kb, llm.openai, llm.anthropic,
        llm.ollama, alerts.webhook, alerts.slack, alerts.pagerduty,
        obs.otel, auth.oidc, vector.pinecone, vector.pgvector, vector.qdrant."""
        return await client.put(f"/v1/admin/integrations/{kind}",
                                  {"name": name or kind,
                                   "enabled": enabled,
                                   "config": config or {}})

    # ----------------------------------------------------------------------
    # COMPLIANCE — frameworks, audit log, DSAR, consent, retention,
    #              breach, risks, AIIA, model cards, profile
    # ----------------------------------------------------------------------

    @mcp.tool()
    async def compliance_overview(tenant_id: str = "") -> dict:
        """Aggregate compliance score across the 13 frameworks
        TrustLens supports."""
        q = f"?tenant_id={tenant_id}" if tenant_id else ""
        return await client.get(f"/v1/admin/compliance/overview{q}")

    @mcp.tool()
    async def compliance_frameworks() -> dict:
        """List the 13 frameworks (GDPR, CCPA, ISO 27001/27701/42001,
        EU AI Act, NIST AI RMF, SOC 2, DORA, Colorado AI, India DPDP,
        China GenAI, Korea AI) with per-framework status."""
        return {"frameworks": await client.get("/v1/admin/compliance/frameworks")}

    @mcp.tool()
    async def framework_detail(framework_id: str) -> dict:
        """Inspect controls and evidence for a single framework. Use
        ``compliance_frameworks`` first to discover ids."""
        return await client.get(f"/v1/admin/compliance/frameworks/{framework_id}")

    @mcp.tool()
    async def audit_log_query(limit: int = 100, action_prefix: str = "",
                               tenant_id: str = "") -> dict:
        """Read the SHA-256 hash-chained audit log."""
        q = [f"limit={limit}"]
        if action_prefix: q.append(f"action_prefix={action_prefix}")
        if tenant_id:     q.append(f"tenant_id={tenant_id}")
        return {"events": await client.get(
            "/v1/admin/compliance/audit-log?" + "&".join(q))}

    @mcp.tool()
    async def audit_log_verify() -> dict:
        """Verify the audit log hash chain end-to-end. Returns
        {ok, n_events, first_break_seq, reason}."""
        return await client.get("/v1/admin/compliance/audit-log/verify")

    @mcp.tool()
    async def dsar_open(tenant_id: str, data_subject_id: str,
                        type: str, jurisdiction: str = "gdpr",
                        contact: str = "") -> dict:
        """Open a DSAR. Types: access, rectify, delete, portability,
        restrict, object, opt_out, limit_use. Jurisdictions: gdpr (30d),
        ccpa (45d), india_dpdp (30d), korea_ai (30d)."""
        return await client.post("/v1/admin/compliance/dsar",
                                  {"tenant_id": tenant_id,
                                   "data_subject_id": data_subject_id,
                                   "type": type, "jurisdiction": jurisdiction,
                                   "contact": contact, "received_via": "mcp"})

    @mcp.tool()
    async def dsar_list(tenant_id: str = "", status: str = "") -> dict:
        """List DSARs. Optionally filter by tenant + status."""
        q = []
        if tenant_id: q.append(f"tenant_id={tenant_id}")
        if status:    q.append(f"status={status}")
        suffix = "?" + "&".join(q) if q else ""
        return {"requests": await client.get(f"/v1/admin/compliance/dsar{suffix}")}

    @mcp.tool()
    async def dsar_fulfill(request_id: str,
                            artifact_uri: Optional[str] = None,
                            note: str = "") -> dict:
        """Mark a DSAR fulfilled. ``artifact_uri`` is where the data
        export / deletion proof lives (S3 path, ticket id, etc.)."""
        return await client.patch(f"/v1/admin/compliance/dsar/{request_id}",
                                   {"status": "fulfilled",
                                    "artifact_uri": artifact_uri,
                                    "note": note})

    @mcp.tool()
    async def dsar_reject(request_id: str, rejection_reason: str) -> dict:
        """Reject a DSAR with a documented lawful reason. Required for
        audit trail."""
        if not rejection_reason:
            return {"status": "needs_input", "required": ["rejection_reason"]}
        return await client.patch(f"/v1/admin/compliance/dsar/{request_id}",
                                   {"status": "rejected",
                                    "rejection_reason": rejection_reason})

    @mcp.tool()
    async def consent_record(tenant_id: str, data_subject_id: str,
                              purpose: str, status: str,
                              lawful_basis: str = "consent",
                              evidence_uri: str = "") -> dict:
        """Append a consent record. Purpose: service_delivery, ai_training,
        personalization, analytics, marketing, third_party_share,
        sensitive_pi. Status: granted, withdrawn, expired."""
        return await client.post("/v1/admin/compliance/consent",
                                  {"tenant_id": tenant_id,
                                   "data_subject_id": data_subject_id,
                                   "purpose": purpose, "status": status,
                                   "lawful_basis": lawful_basis,
                                   "captured_via": "mcp",
                                   "evidence_uri": evidence_uri or None})

    @mcp.tool()
    async def consent_history(tenant_id: str, limit: int = 50) -> dict:
        """List a tenant's consent records."""
        return {"records": await client.get(
            f"/v1/admin/compliance/consent?tenant_id={tenant_id}&limit={limit}")}

    @mcp.tool()
    async def retention_list(tenant_id: str = "") -> dict:
        """List retention policies for a tenant."""
        q = f"?tenant_id={tenant_id}" if tenant_id else ""
        return {"policies": await client.get(f"/v1/admin/compliance/retention{q}")}

    @mcp.tool()
    async def retention_seed(tenant_id: str) -> dict:
        """Seed sensible default retention policies for a tenant."""
        return {"created": await client.post(
            f"/v1/admin/compliance/retention/seed?tenant_id={tenant_id}")}

    @mcp.tool()
    async def retention_set(tenant_id: str, data_class: str,
                             retention_days: int,
                             deletion_method: str = "purge",
                             legal_hold: bool = False,
                             notes: str = "") -> dict:
        """Upsert a retention policy. data_class: certificates, audit_log,
        chat_logs, kb_documents, incidents, bench_events, user_profiles."""
        return await client.put("/v1/admin/compliance/retention",
                                  {"tenant_id": tenant_id,
                                   "data_class": data_class,
                                   "retention_days": retention_days,
                                   "deletion_method": deletion_method,
                                   "legal_hold": legal_hold, "notes": notes})

    @mcp.tool()
    async def breach_open(severity: str, kind: str, title: str,
                           summary: str, jurisdictions: list[str],
                           tenant_id: str = "",
                           affected_subjects_estimate: Optional[int] = None,
                           data_classes: Optional[list[str]] = None) -> dict:
        """Open a breach report. severity: low/medium/high/critical.
        kind: confidentiality, integrity, availability, ai_harm, insider,
        supply_chain. jurisdictions schedule the notification windows
        (gdpr, ccpa, dora, eu_ai_act, hipaa, india_dpdp, korea_ai, sec_cyber)."""
        return await client.post("/v1/admin/compliance/breach",
                                  {"tenant_id": tenant_id, "severity": severity,
                                   "kind": kind, "title": title, "summary": summary,
                                   "jurisdictions": jurisdictions,
                                   "data_classes": data_classes or [],
                                   "affected_subjects_estimate": affected_subjects_estimate})

    @mcp.tool()
    async def breach_overdue() -> dict:
        """List notification windows that have passed without filing."""
        return {"overdue": await client.get("/v1/admin/compliance/breach/overdue")}

    @mcp.tool()
    async def breach_close(breach_id: str, rcca_uri: str = "") -> dict:
        """Close a breach with a root-cause / corrective-action URI."""
        return await client.post(f"/v1/admin/compliance/breach/{breach_id}/close",
                                  {"rcca_uri": rcca_uri or None})

    @mcp.tool()
    async def risks_list(tenant_id: str = "") -> dict:
        """List AI risk register items."""
        q = f"?tenant_id={tenant_id}" if tenant_id else ""
        return {"risks": await client.get(f"/v1/admin/compliance/risks{q}")}

    @mcp.tool()
    async def risks_seed(tenant_id: str = "") -> dict:
        """Seed a starter AI risk register (hallucination, prompt injection,
        PII leak, discrimination, automation bias, opacity)."""
        suffix = f"?tenant_id={tenant_id}" if tenant_id else ""
        return {"created": await client.post(
            f"/v1/admin/compliance/risks/seed{suffix}")}

    @mcp.tool()
    async def aiia_create(system_name: str, intended_purpose: str,
                          deployed_geographies: list[str],
                          affected_groups: list[str],
                          human_oversight_summary: str,
                          monitoring_summary: str,
                          tenant_id: str = "") -> dict:
        """Create an AI Impact Assessment (DPIA / Colorado AIA / EU AI
        Act Art.9 / ISO 42001 8.4). EU+'employment' / 'credit' / etc.
        auto-classifies as high-risk."""
        return await client.post("/v1/admin/compliance/aiia",
                                  {"tenant_id": tenant_id,
                                   "system_name": system_name,
                                   "intended_purpose": intended_purpose,
                                   "deployed_geographies": deployed_geographies,
                                   "affected_groups": affected_groups,
                                   "human_oversight_summary": human_oversight_summary,
                                   "monitoring_summary": monitoring_summary})

    @mcp.tool()
    async def model_cards_list(tenant_id: str = "") -> dict:
        """List versioned AI model cards."""
        q = f"?tenant_id={tenant_id}" if tenant_id else ""
        return {"cards": await client.get(f"/v1/admin/compliance/model-cards{q}")}

    @mcp.tool()
    async def model_card_create(system_name: str, provider: str, model_id: str,
                                 intended_use: str, contact: str,
                                 out_of_scope_use: Optional[list[str]] = None,
                                 tenant_id: str = "") -> dict:
        """Create a model card (ISO 42001 A.6.1.2 / EU AI Act Art.11 /
        NIST AI RMF MAP)."""
        return await client.post("/v1/admin/compliance/model-cards",
                                  {"tenant_id": tenant_id,
                                   "system_name": system_name,
                                   "provider": provider, "model_id": model_id,
                                   "intended_use": intended_use,
                                   "contact": contact,
                                   "out_of_scope_use": out_of_scope_use or []})

    @mcp.tool()
    async def profile_get(tenant_id: str) -> dict:
        """Read the per-tenant compliance profile (DPO, lawful basis,
        jurisdictions, applicable frameworks, etc.)."""
        return await client.get(f"/v1/admin/compliance/profile/{tenant_id}")

    @mcp.tool()
    async def profile_update(tenant_id: str,
                              legal_name: Optional[str] = None,
                              dpo_contact: Optional[str] = None,
                              lawful_basis: Optional[str] = None,
                              purposes_of_processing: Optional[list[str]] = None,
                              deployment_geographies: Optional[list[str]] = None,
                              applicable_frameworks: Optional[list[str]] = None,
                              breach_reporting_jurisdictions: Optional[list[str]] = None,
                              is_high_risk_ai: Optional[bool] = None) -> dict:
        """Patch the per-tenant compliance profile. The profile feeds RoPA
        generation and breach window selection."""
        body = {k: v for k, v in {
            "legal_name": legal_name, "dpo_contact": dpo_contact,
            "lawful_basis": lawful_basis,
            "purposes_of_processing": purposes_of_processing,
            "deployment_geographies": deployment_geographies,
            "applicable_frameworks": applicable_frameworks,
            "breach_reporting_jurisdictions": breach_reporting_jurisdictions,
            "is_high_risk_ai": is_high_risk_ai,
        }.items() if v is not None}
        if not body:
            return {"status": "noop"}
        return await client.put(f"/v1/admin/compliance/profile/{tenant_id}", body)

    @mcp.tool()
    async def transparency_ropa(tenant_id: str) -> dict:
        """Generate a GDPR Art.30 Records of Processing Activities."""
        return await client.get(f"/v1/admin/compliance/transparency/{tenant_id}/ropa")

    @mcp.tool()
    async def transparency_eu_ai_act(tenant_id: str) -> dict:
        """Generate the EU AI Act Art.13/26 deployer information packet."""
        return await client.get(f"/v1/admin/compliance/transparency/{tenant_id}/eu-ai-act")

    # ----------------------------------------------------------------------
    # GUIDED SETUP — top-level flow the agent walks the user through
    # ----------------------------------------------------------------------

    @mcp.tool()
    async def setup_status() -> dict:
        """Return what's set up vs missing. Use this to drive the
        ``trustlens-setup`` skill conversation."""
        out: dict = {"status": "ok", "checks": {}}
        # 1. gateway up
        try:
            await client.get("/healthz")
            out["checks"]["gateway"] = True
        except Exception:
            out["checks"]["gateway"] = False
            out["status"] = "incomplete"
            out["next_action"] = "Run `trustlens serve-gateway` first."
            return out
        # 2. signed in
        me = await client.whoami()
        out["checks"]["authenticated"] = me.get("authenticated", False)
        # 3. tenants registered
        try:
            tenants = await client.get("/v1/admin/tenants")
            out["checks"]["tenants_count"] = len(tenants)
        except Exception:
            out["checks"]["tenants_count"] = "?"
        # 4. KB loaded for default tenant
        try:
            status = await client.get(f"/v1/kb/status?tenant_id={client.tenant_id}")
            out["checks"]["kb_size"] = status.get("index_size", 0)
        except Exception:
            out["checks"]["kb_size"] = 0
        # 5. compliance profile set
        try:
            prof = await client.get(f"/v1/admin/compliance/profile/{client.tenant_id}")
            out["checks"]["profile_legal_name_set"] = bool(prof.get("legal_name"))
            out["checks"]["profile_dpo_set"] = bool(prof.get("dpo_contact"))
        except Exception:
            out["checks"]["profile_legal_name_set"] = False
            out["checks"]["profile_dpo_set"] = False
        # 6. risks seeded
        try:
            r = await client.get("/v1/admin/compliance/risks")
            out["checks"]["risk_register_seeded"] = len(r) >= 6
        except Exception:
            out["checks"]["risk_register_seeded"] = False
        # 7. retention seeded
        try:
            r = await client.get("/v1/admin/compliance/retention")
            out["checks"]["retention_seeded"] = len(r) >= 6
        except Exception:
            out["checks"]["retention_seeded"] = False

        # Recommend next step
        c = out["checks"]
        if not c.get("authenticated"):
            out["next_action"] = "Call `login` with email + password (default: owner@trustlens.local / trustlens)."
        elif c.get("tenants_count", 0) == 0:
            out["next_action"] = "Use the gateway env vars to register at least one tenant. See docs/INTEGRATION.md."
        elif c.get("kb_size", 0) == 0:
            out["next_action"] = "Use `kb_upsert` to load your knowledge base documents."
        elif not c.get("profile_legal_name_set"):
            out["next_action"] = "Use `profile_update` to set legal_name + dpo_contact."
        elif not c.get("risk_register_seeded"):
            out["next_action"] = "Use `risks_seed` to populate the AI risk register."
        elif not c.get("retention_seeded"):
            out["next_action"] = "Use `retention_seed` for the default retention policies."
        else:
            out["next_action"] = "Setup is complete. You can now operate, audit, and tune."
        return out

    @mcp.tool()
    async def quick_start_demo(tenant_id: str = "demo") -> dict:
        """One-shot demo: load a small KB, run a verified chat, return
        the cert. Useful as the very first thing an agent shows the user."""
        # Load 2 demo docs
        docs = [
            {"doc_id": "fr", "text": "Paris is the capital of France.", "source_uri": "kb://fr"},
            {"doc_id": "wt", "text": "Water boils at 100 degrees Celsius at sea level."},
        ]
        await client.post("/v1/admin/kb/upsert",
                           {"tenant_id": tenant_id, "documents": docs})
        chat = await client.post("/v1/chat/completions", {
            "model": "echo",
            "messages": [{"role": "user",
                          "content": "What is the capital of France?"}],
            "trustlens": {"verification_tier": "standard"},
        })
        return {"demo": True, "kb_loaded": len(docs), "chat": chat}

    # ----------------------------------------------------------------------
    # RESOURCES — read-only documents the agent can fetch
    # ----------------------------------------------------------------------

    @mcp.resource("trustlens://compliance/overview")
    async def res_compliance_overview() -> str:
        """Live compliance overview as JSON."""
        import json
        try:
            d = await client.get("/v1/admin/compliance/overview")
        except Exception as e:
            d = {"error": str(e)}
        return json.dumps(d, indent=2)

    @mcp.resource("trustlens://help/getting-started")
    async def res_getting_started() -> str:
        """Operator getting-started cheatsheet."""
        return (
            "# TrustLens — getting started for an MCP-driven agent\n\n"
            "1. `trustlens_version()` to confirm the gateway is reachable\n"
            "2. `login('owner@trustlens.local', 'trustlens')` for dev\n"
            "3. `setup_status()` — returns which steps still need doing\n"
            "4. `quick_start_demo()` — load 2 KB docs + verify a chat\n"
            "5. `compliance_overview()` — see regulation coverage\n\n"
            "Key safety rule: always confirm with the user before any\n"
            "tool whose docstring says 'Destructive' or 'confirm with user'."
        )

    return mcp
