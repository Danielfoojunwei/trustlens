# GDPR — step-by-step

EU General Data Protection Regulation (Regulation 2016/679). Applies to
controllers and processors handling personal data of EU/EEA residents.

## TrustLens score: 0.85 / 1.00 · 17 mapped controls

## Step 1 — Identify your role

Open **COMPLIANCE → TENANT PROFILE** in the dashboard.

| Field | Notes |
|---|---|
| Legal name | the legal entity that decides means and purposes |
| Address | for Art.13 notice |
| DPO contact | required if Art.37 applies — public, monitored email |
| EU representative (Art.27) | if you have no EU establishment |
| Lawful basis | one of: contract / consent / legal_obligation / vital_interests / public_task / legitimate_interests |
| Purposes of processing | one or more from your activities register |

Save. The dashboard generates a draft RoPA from this profile (Art.30).

## Step 2 — Records of Processing Activities (Art.30)

Click ↓ RoPA in the profile view. The downloaded JSON contains:

- Controller identity + DPO + EU rep
- Purposes + lawful basis
- Categories of data + subjects
- Recipients (sub-processors)
- Cross-border transfer basis (SCC / adequacy / DPF)
- Retention policies per data class (driven by COMPLIANCE → RETENTION)
- Security measures (Ed25519 certs, hash-chained audit log, RBAC, etc.)

Update annually or on material change.

## Step 3 — Data Subject Rights (Arts. 15-22)

Open **COMPLIANCE → DSAR QUEUE**. Every right has a button:

| Right | Article | DSAR type |
|---|---|---|
| Access | Art.15 | `access` |
| Rectification | Art.16 | `rectify` |
| Erasure ("right to be forgotten") | Art.17 | `delete` |
| Restriction | Art.18 | `restrict` |
| Portability | Art.20 | `portability` |
| Object | Art.21 | `object` |
| Automated decision-making | Art.22 | open via `restrict` + flag for human review |

SLA defaults to 30 days (GDPR Art.12(3)). The dashboard shows
**days remaining** for each open DSAR and auto-flags overdue items
(status → `overdue`).

## Step 4 — Consent management (Art.7)

Open **COMPLIANCE → CONSENT**. Record one row per (subject, purpose):

```json
{
  "tenant_id":"acme",
  "data_subject_id":"hash:abcdef",
  "purpose":"ai_training",
  "status":"granted",
  "lawful_basis":"consent",
  "captured_via":"ui",
  "evidence_uri":"s3://acme-consents/2026-04/abcdef.json"
}
```

Withdrawals are appended (`status: withdrawn`), never overwriting.
The history per subject is queryable through the dashboard.

## Step 5 — Retention (Art.5(e))

Open **COMPLIANCE → RETENTION**. Click ⚙ SEED DEFAULTS. Then review and
adjust each policy. Defaults:

| Data class | Days | Method |
|---|---|---|
| certificates | 2,555 (7y) | purge |
| audit_log | 2,555 (7y) | purge |
| chat_logs | 180 | anonymize |
| kb_documents | 1,095 | purge |
| incidents | 730 | purge |
| user_profiles | 365 | anonymize |

Toggle **legal_hold** per row to suspend automatic deletion when litigation
or regulatory hold applies.

## Step 6 — Breach notification (Arts. 33-34)

If you detect a breach, open **COMPLIANCE → BREACH REPORTS** → OPEN A
BREACH. Tag the jurisdiction `gdpr` to enable both:

- **gdpr_dpa** window — 72h to supervisory authority
- **gdpr_subjects** window — without undue delay to data subjects (if high
  risk to rights and freedoms)

The dashboard shows real-time countdown. Click **Notified** when the
relevant filing is sent.

## Step 7 — DPIA (Art.35)

For high-risk processing, open **COMPLIANCE → RISK REGISTER** → CREATE
AIIA. Fields map directly to Art.35(7):

- intended purpose
- assessment of necessity / proportionality (notes / monitoring)
- risks to rights and freedoms (link to risk register items)
- measures envisaged (mitigations + human oversight)

Each AIIA carries a 365-day next-review date.

## Step 8 — Cross-border transfers (Arts. 44-49)

Set the profile's **cross_border_basis** to one of:

- `SCC` — Standard Contractual Clauses (2021 modules)
- `adequacy` — Commission adequacy decision
- `DPF` — EU-US Data Privacy Framework
- `BCR` — Binding Corporate Rules

Document the Transfer Impact Assessment in the **notes** field. SCC
ledger UI is NEAR-term.

## Step 9 — Audit + verification

The hash-chained audit log records every privacy-relevant action:
DSAR open/fulfil/reject, consent record, retention change, breach
filing. Click **∎ VERIFY CHAIN** in COMPLIANCE → AUDIT LOG before
exporting evidence. Export as JSONL or CSV.

## Step 10 — Annual review

Run through the entire **COMPLIANCE → FRAMEWORK GRID** for `gdpr`. Any
control with status `partial` or `near` is your work list. Re-run the
DPIA for high-risk processing on the next-review date.

## Penalties for non-compliance

Up to **€20m** or **4%** of global annual turnover (Art.83(5)),
whichever is higher.
