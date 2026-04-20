---
name: trustlens-compliance
description: Manage TrustLens compliance posture across 13 frameworks (GDPR, CCPA, ISO 27001/27701/42001, EU AI Act, NIST AI RMF, SOC 2, DORA, Colorado AI, India DPDP, China GenAI, Korea AI). Handle DSARs, consent, retention, breaches, AI risks, model cards, AIIA. Use whenever the user asks about a regulation, opens a DSAR, reports a breach, or asks "are we compliant".
---

# TrustLens compliance

## When the user asks "are we compliant?"

1. `compliance_overview(tenant_id)` → mean score across applicable frameworks.
2. `compliance_frameworks()` → per-framework SHIP/PARTIAL/NEAR/GAP.
3. Report **only the frameworks they're subject to** — fetch
   `profile_get(tenant_id)` and filter to `applicable_frameworks`.
4. Highlight any framework with score < 0.80 + the top 3 PARTIAL or
   NEAR controls (drill via `framework_detail(framework_id)`).

## DSAR workflow (right of access / erasure / portability / etc.)

Common ask: *"Process a deletion request from john@example.com."*

1. Generate a stable opaque `data_subject_id` (e.g. `hash:` + sha256 of
   email). **Don't store the email in the DSAR record itself.**
2. `dsar_open(tenant_id, data_subject_id, type="delete", jurisdiction="gdpr")`.
3. Confirm SLA (`30d` for GDPR, `45d` for CCPA, `30d` for India DPDP).
4. Walk the user through the actual deletion: which datastores (KB
   docs, chat logs, profile, oracle caches) hold their data.
5. After deletion is done, `dsar_fulfill(request_id, artifact_uri="...")`.
   The artifact_uri should point at the deletion proof (S3 path,
   ticket id, CRM note).

If the user is rejecting (legal exemption / pseudonymization):
`dsar_reject(request_id, rejection_reason="Art.17(3)(b) legal obligation")`.

## Consent workflow

The user typically has 3 patterns:

- **Record new consent**: ask `tenant_id`, `data_subject_id` (opaque),
  `purpose` (default `service_delivery`), `lawful_basis` (default
  `consent`), and `evidence_uri` (where the proof lives). Then
  `consent_record(...)`.
- **Withdraw consent**: same call with `status="withdrawn"`. Critically,
  do not delete the prior `granted` row — the history is the audit trail.
- **Audit history**: `consent_history(tenant_id)`.

## Retention

For a fresh tenant: `retention_seed(tenant_id)` → 7 sane defaults
(certs 7y, audit log 7y, chat logs 180d w/ anonymize, KB 3y, etc.).
Then walk the user through any class they need to tighten or extend.

`retention_set(...)` upserts. `legal_hold=true` suspends auto-deletion
when litigation/regulatory hold applies.

## Breach reporting

When the user reports a breach, classify before opening the report:

- **severity**: low / medium / high / critical
- **kind**: confidentiality / integrity / availability / ai_harm /
  insider / supply_chain
- **jurisdictions**: ask which apply. Default for EU+EEA is
  `["gdpr"]`; financial entities also `["dora"]`; healthcare also
  `["hipaa"]`; AI-output incidents also `["eu_ai_act"]`.

Then `breach_open(...)`. The tool returns the per-jurisdiction
notification windows (4h DORA, 72h GDPR, 15d EU AI Act, 60d HIPAA,
45d CCPA). Surface the soonest deadline first.

When notification is filed: `mark_notified` (via direct REST), or close
the breach: `breach_close(breach_id, rcca_uri="...")`.

## AI risk register + AIIA

For new AI deployments:

1. `risks_seed(tenant_id)` → 6 starter risks tagged with framework refs.
2. `aiia_create(...)` to produce an Algorithmic Impact Assessment. The
   tool auto-classifies as `high` if the deployment matches EU AI Act
   Annex III (employment, credit, education, infrastructure, law
   enforcement, migration, biometric).

## Model cards

`model_card_create(...)` for every distinct AI system the user ships.
Required for ISO 42001 A.6.1.2 / EU AI Act Art.11 / NIST AI RMF MAP.

## Audit trail integrity

After any compliance work, run `audit_log_verify()`. The chain must
report `ok: true`. If broken (`first_break_seq` set), STOP and warn:
the audit log has been tampered with — do not continue any operations
until investigated.

## Transparency artifacts

- `transparency_ropa(tenant_id)` → GDPR Art.30 RoPA JSON.
- `transparency_eu_ai_act(tenant_id)` → EU AI Act Art.13/26 packet.
