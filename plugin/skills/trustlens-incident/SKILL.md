---
name: trustlens-incident
description: Triage and respond to a live incident — SSH critical alarm, RAD-CoT engagement, oracle outage, blocked-cert spike, breach. Use whenever the user reports an alert, asks "what's wrong with the gateway", or wants to walk an active incident through to closure.
---

# Incident response

## Triage flow (always start here)

1. `axes_summary()` — is the system in a healthy regime? Compare against
   the user's baseline expectations.
2. `incidents_list(acked=False)` — open incidents.
3. `audit_log_verify()` — chain integrity. If broken, escalate
   immediately and stop further automated changes.
4. Sort the incidents by severity (`critical` first) and `kind`. Group
   them into root-cause buckets.

## Common incident kinds

### `ssh.critical` (DEEP tier)
- Cause: attention matrices entered an unstable regime. May indicate
  an injected/jailbreak prompt, a model regression, or threshold drift.
- Action sequence:
  1. Look at the `cert_id` in `detail` and `verify_certificate(cert_id)`.
  2. Inspect the `deep_inspector.ssh_snapshots` series — find the step
     where ρ crossed `threshold_rho`.
  3. If the cert is grounded (verdict VERIFIED on most claims), it's a
     false-positive stability alarm — propose
     `settings_update(ssh_threshold_rho=current+0.01)`.
  4. If the cert is BLOCKED, this is a real catch — the system did
     its job. Ack the incident with a note linking the cert.

### `radcot.engage`
- Cause: SSH critical fired and steering kicked in.
- Action: confirm the model output stayed coherent. If quality
  regressed, lower `steering_alpha`.

### `oracle.outage` / `oracle.slow`
- Cause: a registered oracle (Wikidata, vector DB, internal API) is
  failing or slow.
- Action sequence:
  1. `integrations_list()` — find the failing oracle.
  2. Probe its health out-of-band (curl, internal monitoring).
  3. Temporarily disable: `integration_set(kind, enabled=False)`. The
     verifier will return DEGRADED certs but stay live.
  4. When restored, re-enable.

### `block_rate.spike`
- Cause: a sudden surge in BLOCKED certs.
- Action sequence:
  1. `axes_summary(window_s=60)` — check the external axis. If it
     dropped, an oracle is down.
  2. `incidents_list(kind="oracle.outage")` — confirm.
  3. If oracles are healthy, look at recent `kb.delete` or `kb.revert`
     audit events: someone may have removed grounding docs.
     `audit_log_query(action_prefix="kb.")` to find.

### `budget.exhausted`
- Cause: a tenant hit `max_rps` or `max_tokens_per_minute`.
- Action: ask the user whether to raise the limit (operational decision)
  or hold. The MCP server can't change tenant config today — direct the
  user to the dashboard or `TenantConfig` code.

## When the user says "we have a security breach"

1. Triage: `breach_open(severity, kind, title, summary, jurisdictions=...)`.
2. Surface the per-jurisdiction notification windows. The shortest
   wins (DORA initial = 4h, GDPR DPA = 72h, CCPA = 45d, etc.).
3. Confirm classification: `severity` + `affected_subjects_estimate`
   determine "major" vs "minor" under DORA / GDPR.
4. Walk the user through the notification process for each window. Mark
   each notified via REST.
5. When root cause identified: `breach_close(breach_id, rcca_uri="<link to RCCA doc>")`.

## Post-incident hygiene

- `audit_log_verify()` again — confirm the chain is still intact.
- Suggest writing/updating a model card if the incident revealed a
  new failure mode.
- Suggest updating the risk register if the incident indicates a
  likelihood/impact shift.
