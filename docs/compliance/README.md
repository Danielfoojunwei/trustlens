# Compliance — step-by-step guides

TrustLens ships against 13 regulatory frameworks out of the box. Each guide
in this directory is a self-contained checklist mapping the regulation to
the exact dashboard / CLI / API actions an operator takes.

Index:

| Guide | Regulation | Jurisdiction |
|---|---|---|
| [GDPR.md](GDPR.md) | EU General Data Protection Regulation | EU/EEA |
| [CCPA.md](CCPA.md) | California Consumer Privacy Act / CPRA | US-CA |
| [ISO_27001.md](ISO_27001.md) | Information Security Management System | International |
| [ISO_27701.md](ISO_27701.md) | Privacy Information Management System | International |
| [ISO_42001.md](ISO_42001.md) | AI Management System | International |
| [EU_AI_ACT.md](EU_AI_ACT.md) | EU AI Act (Reg. 2024/1689) | EU/EEA |
| [NIST_AI_RMF.md](NIST_AI_RMF.md) | NIST AI Risk Management Framework 2.0 | US (voluntary) |
| [SOC_2.md](SOC_2.md) | SOC 2 Type II Trust Services Criteria | US (AICPA) |
| [DORA.md](DORA.md) | EU Digital Operational Resilience Act | EU finance |
| [COLORADO_AI.md](COLORADO_AI.md) | Colorado AI Act (SB 24-205) | US-CO |
| [INDIA_DPDP.md](INDIA_DPDP.md) | Digital Personal Data Protection Act + IT Rules 2026 | India |
| [CHINA_GENAI.md](CHINA_GENAI.md) | Interim Measures for Generative AI Services | China |
| [KOREA_AI.md](KOREA_AI.md) | Framework Act on AI (2026) | South Korea |

Universal first steps regardless of regulation:

1. **Set the tenant compliance profile.** Open the dashboard's COMPLIANCE
   → TENANT PROFILE view, fill in legal name / DPO contact / lawful basis
   / jurisdictions, save.
2. **Seed default risks + retention.** Click ⚙ SEED DEFAULTS in both
   COMPLIANCE → RISK REGISTER and COMPLIANCE → RETENTION.
3. **Enable the audit log integrity check.** Open COMPLIANCE → AUDIT LOG
   and click ∎ VERIFY CHAIN. The chain must be unbroken — any mutation in
   transit is detectable.
4. **Confirm framework grid is green for the regulations you're subject
   to.** COMPLIANCE → FRAMEWORK GRID lists 13; click VIEW on each one
   relevant to your deployment to drill into the per-control evidence.

The compliance subsystem is wired so every mutation (KB upsert, retention
policy change, risk acceptance, breach close, model card edit) flows into
the SHA-256 hash-chained audit log. Auditors verify the chain offline with
``trustlens compliance verify-chain ./audit-log.jsonl --public-key ...``
(NEAR-term CLI; today, use the dashboard's ∎ VERIFY CHAIN button or
``GET /v1/admin/compliance/audit-log/verify``).
