# Compliance playbooks (12 regulations) — short-form

Each section is a copy-paste cheat sheet. The full GDPR walkthrough lives
in [GDPR.md](GDPR.md); this file follows the same shape for every other
framework TrustLens supports today.

> **Universal first 4 steps** for every regulation:
>
> 1. **TENANT PROFILE** — fill legal name, DPO/contact, jurisdictions.
> 2. **AUDIT LOG** — click ∎ VERIFY CHAIN, must show "unbroken".
> 3. **RISK REGISTER** — click ⚙ SEED DEFAULTS, tighten any risk that
>    matters in your context.
> 4. **RETENTION** — click ⚙ SEED DEFAULTS, then adjust per data class.

---

## CCPA / CPRA — California Consumer Privacy Act

| Right | Code | Steps |
|---|---|---|
| Know | 1798.100 / 110 | DSAR type=`access`, jurisdiction=`ccpa` (45-day SLA) |
| Delete | 1798.105 | DSAR type=`delete` |
| Correct | 1798.106 | DSAR type=`rectify` |
| Opt-out of sale/sharing | 1798.120 | CONSENT → record `purpose=third_party_share, status=withdrawn` |
| Limit use of sensitive PI | 1798.121 | CONSENT → `purpose=sensitive_pi`; PROFILE → flag `sensitive_pi=true` |
| Notice at collection | 1798.130 | TENANT PROFILE → ↓ Privacy notice (auto-generated draft) |
| Breach (45-day notice) | 1798.150 | BREACH REPORTS → jurisdictions=`ccpa`; window auto-tracked |

Penalty up to **$7,500 per intentional violation** + private right of
action for security breaches.

---

## ISO/IEC 27001:2022 — Information Security Management System

Use the **FRAMEWORK GRID** to walk Annex A controls. TrustLens score 0.80.

| Control | Where it lives |
|---|---|
| A.5.15 Access control | USERS & ROLES |
| A.5.24-26 Incident management | INCIDENTS + BREACH REPORTS |
| A.5.28 Collection of evidence | AUDIT LOG (hash chain) |
| A.5.30 ICT readiness | Circuit breakers + chaos suite |
| A.8.5 Secure authentication | Sessions + API keys |
| A.8.10 Information deletion | RETENTION |
| A.8.15 Logging | AUDIT LOG + Prometheus /metrics |
| A.8.16 Monitoring | OVERVIEW + 3-AXIS LIVE |
| A.8.24 Cryptography | Ed25519 certs (`trustlens verify`) |
| A.8.32 Change management | AUDIT LOG entries on every mutation |

Externally-attested ISMS is a **6-month observation window** away — see
`docs/ENTERPRISE.md §3 Compliance posture`.

---

## ISO/IEC 27701:2019 — Privacy Information Management System

Extension of 27001 for PII. TrustLens score 0.83.

- **A.7.2.1-2** Purpose + lawful basis → TENANT PROFILE
- **A.7.2.3-4** Consent capture + history → CONSENT
- **A.7.2.5** PIA → RISK REGISTER (CREATE AIIA)
- **A.7.3.1-3** PII principal information + modification → CONSENT history
  + DSAR
- **A.7.4.5** PII de-identification at end of processing → RETENTION
  (`deletion_method=anonymize`)
- **A.8.2.1** Customer agreement (DPA) → out of TrustLens scope; supply
  your own DPA template referencing the controls above.

---

## ISO/IEC 42001:2023 — AI Management System

The "ISO 27001 for AI." TrustLens score 0.91 (highest).

| Clause | Where |
|---|---|
| 6.1 Risk-based approach | RISK REGISTER |
| 8.2 AI risk assessment | RISK REGISTER + AIIA |
| 8.3 AI risk treatment | Verifier engine + Deep Inspector |
| 8.4 AI system impact assessment | RISK REGISTER → CREATE AIIA |
| 9.1 Monitoring | OVERVIEW + 3-AXIS LIVE + Prometheus |
| 9.2 Internal audit | AUDIT LOG (∎ VERIFY CHAIN) |
| 10.2 Nonconformity & corrective action | INCIDENTS + BREACH REPORTS |
| A.6.1.2 AI documentation | MODEL CARDS |
| A.7.2 Data resources | KNOWLEDGE BASE (versioned) |
| A.9.2 AI incident reporting | BREACH REPORTS (kind=`ai_harm`) |

Recommended cadence: AIIA per system, quarterly review of risks.

---

## EU AI Act (Reg. 2024/1689) — full application Aug 2026

TrustLens score 0.79. High-risk AI obligations (Annex III) are the bar.

Step-by-step:

1. **TENANT PROFILE** → flag `is_high_risk_ai = true` if your deployment
   matches Annex III (employment, credit, education, critical infra,
   law enforcement, migration, biometric, essential services).
2. **MODEL CARDS** → create a card per AI system (Art.11 technical
   documentation + Art.13 transparency).
3. **RISK REGISTER → CREATE AIIA** → fields populate the Art.9 risk
   management system; `is_high_risk_eu_ai_act()` auto-classifies.
4. **AUDIT LOG** → satisfies Art.12 record-keeping. Verify monthly.
5. **3-AXIS LIVE** → satisfies Art.15 accuracy / robustness monitoring.
6. **INCIDENTS / BREACH REPORTS** → kind=`ai_harm`, jurisdiction=`eu_ai_act`
   for Art.73 serious-incident reporting (15-day window).
7. **TENANT PROFILE → ↓ EU AI Act packet** generates the Art.13/26
   deployer information packet.

Penalty up to **€35m** or **7%** of global turnover for prohibited-use
violations; up to **€15m / 3%** for high-risk obligations.

---

## NIST AI Risk Management Framework 2.0 — voluntary US

TrustLens score 0.95 (highest). Walk the four functions:

| Function | TrustLens action |
|---|---|
| **GOVERN** | TENANT PROFILE + USERS & ROLES + RISK REGISTER |
| **MAP** | MODEL CARDS + AIIA + KB versioning |
| **MEASURE** | 3-AXIS LIVE + REDUCTION + ANALYTICS + benchmark scorecards |
| **MANAGE** | INCIDENTS + BREACH REPORTS + AUDIT LOG |

Designed to compose with ISO 42001 — they share most concepts.

---

## SOC 2 Type II — Trust Services Criteria

Score 0.79. Plan: ramp the partial controls to ship over the
**6-month observation window**.

Common-criteria mapping:

- CC3 Risk assessment → RISK REGISTER
- CC6 Logical access → USERS & ROLES + API KEYS + RBAC
- CC7 System operations → INCIDENTS + AUDIT LOG
- CC8 Change management → AUDIT LOG (every mutation captured)
- A1 Availability → CIRCUIT BREAKERS + chaos suite
- PI1 Processing integrity → Ed25519 signed certificates
- C1 Confidentiality → Tenant isolation + RBAC
- P series Privacy → entire COMPLIANCE section

Engage an AICPA-licensed auditor to start the observation window.

---

## DORA — EU Digital Operational Resilience Act (financial entities)

TrustLens score 0.86. Effective Jan 2025 / Jan 2026.

| Article | Action |
|---|---|
| Art.5/9 ICT risk framework | RISK REGISTER → seed defaults |
| Art.11 Detection | INCIDENTS + 3-AXIS LIVE + Prometheus alerts |
| Art.12 Response & recovery | CIRCUIT BREAKERS + retry logic |
| Art.17/18 Incident management + classification | BREACH REPORTS (kind, severity) |
| Art.19 Major incident reporting | BREACH REPORTS → jurisdictions=`dora` triggers 4h/72h/30d windows |
| Art.24 Resilience testing programme | NEAR-term — wire your TLPT here |
| Art.28 Third-party risk | INTEGRATIONS register |

---

## Colorado AI Act (SB 24-205) — effective Feb 2026

Score 0.92. Specifically for **high-risk AI** in employment / lending /
education / housing / insurance / health / legal / government.

1. **PROFILE → flag `is_high_risk_ai`**.
2. **RISK REGISTER → CREATE AIIA** (Annual Algorithmic IA per § 6-1-1703).
3. **3-AXIS LIVE → sycophancy + bias proxies** for §6-1-1702 reasonable
   care to avoid algorithmic discrimination.
4. **MODEL CARDS** for §6-1-1704 consumer notice.
5. **DSAR type=`opt_out` / `restrict`** for §6-1-1705 right to human
   review.
6. **BREACH REPORTS** → jurisdiction=`co` for §6-1-1706 disclosure to AG.

---

## India DPDP Act 2023 + IT Amendment Rules 2026

Score 1.00 (highest). India-specific notes:

- **PROFILE → flag `is_significant_data_fiduciary = true`** if you process
  large volumes / sensitive PI (Sec.10).
- **CONSENT** with `lawful_basis = consent` is the default lawful basis.
- **DSAR** with jurisdiction=`india_dpdp` (30-day SLA).
- **BREACH REPORTS** with jurisdiction=`india_dpdp` triggers 72h to DPB.

Penalty up to **₹250 crore** per instance for failure to safeguard data.

---

## China — Interim Measures for Generative AI Services

Score 0.50 (work in progress). Required actions today:

1. **Real-name registration** of users via the auth system → already in
   the User model.
2. **Training-data lawfulness** → KB versioning + RoPA documentation.
3. **AI-content labeling/watermark** → NEAR-term (Art.12).
4. **Algorithm registration** with CAC → operational, out of TrustLens
   scope; documentation lives in the MODEL CARD.
5. **Illegal-content reporting** → BREACH REPORTS kind=`ai_harm`.

---

## South Korea — Framework Act on AI (effective 2026)

Score 0.90.

| Article | Action |
|---|---|
| Art.13 Risk assessment | RISK REGISTER + AIIA |
| Art.14 Transparency | MODEL CARDS + TENANT PROFILE notice |
| Art.15 Human oversight | per-tier verifier; PROFILE notes |
| Art.16 Explainability | Per-claim verdicts in every cert |
| Art.17 Incident reporting | BREACH REPORTS jurisdiction=`korea_ai` |

---

## Summary score grid (live snapshot at build time)

The dashboard's **COMPLIANCE → FRAMEWORK GRID** is always authoritative
and refreshes on demand. Latest snapshot:

| Framework | Score | Ship | Partial | Near | Gap |
|---|---|---|---|---|---|
| GDPR | 0.85 | 13 | 3 | 1 | 0 |
| CCPA / CPRA | 0.94 | 7 | 1 | 0 | 0 |
| ISO 27001 | 0.80 | 15 | 5 | 2 | 0 |
| ISO 27701 | 0.83 | 9 | 1 | 2 | 0 |
| ISO 42001 | 0.91 | 12 | 4 | 0 | 0 |
| EU AI Act | 0.79 | 8 | 3 | 1 | 0 |
| NIST AI RMF | 0.95 | 17 | 2 | 1 | 0 |
| SOC 2 | 0.79 | 17 | 3 | 1 | 0 |
| DORA | 0.86 | 8 | 1 | 2 | 0 |
| Colorado AI | 0.92 | 5 | 1 | 0 | 0 |
| India DPDP | 1.00 | 8 | 0 | 0 | 0 |
| China GenAI | 0.50 | 2 | 4 | 2 | 0 |
| Korea AI | 0.90 | 4 | 1 | 0 | 0 |

Mean across all 13 frameworks: **0.84**.
