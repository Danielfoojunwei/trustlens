# TrustLens Agentic Skill

TrustLens ships a **control surface** that any agentic harness (Claude
Agent SDK, Claude Code, OpenAI Agents, LangGraph, custom) can drive on
behalf of a human operator. This document is the contract between the
harness and the gateway.

The product pitch for the user is: *"Talk to your agent. The agent talks
to TrustLens. You never touch a dashboard."*

---

## 1. What the agent can do

The agent can, over HTTPS against a running gateway:

| # | Capability | HTTP | Permission |
|---|---|---|---|
| 1 | Read a one-shot status snapshot of the deployment | `GET /v1/agent/status` | `view.overview` |
| 2 | List tenants and their quotas | `GET /v1/agent/tenants` | `view.overview` |
| 3 | Create or update a tenant (tier, RPS, allowed backends/oracles) | `POST /v1/agent/tenants` | `integrations.write` |
| 4 | Upsert knowledge base documents for a tenant | `POST /v1/agent/kb/upsert` | `kb.write` |
| 5 | Delete KB documents | `POST /v1/agent/kb/delete` | `kb.delete` |
| 6 | List / filter incidents | `GET /v1/agent/incidents` | `view.incidents` |
| 7 | Acknowledge an incident | `POST /v1/agent/incidents/{id}/ack` | `incidents.ack` |
| 8 | Read alert rules | `GET /v1/agent/alerts` | `view.incidents` |
| 9 | Set alert rules (block rate, verify latency, SSH-critical, etc.) | `PUT /v1/agent/alerts` | `incidents.webhook` |
| 10 | Re-verify a signed certificate | `POST /v1/agent/verify` | `view.certs` |
| 11 | Discover these capabilities at runtime | `GET /v1/agent/capabilities` | none |

Every mutation writes a SHA-256–chained entry to the audit log, so any
action taken through the agent is independently verifiable after the fact.

## 2. What the agent should proactively ask the user

Before the agent ever calls the gateway, it needs the following from the
user. The harness **must** ask for each missing item exactly once:

1. **Gateway base URL.** Example: `https://trustlens.acme.corp`. Refuse
   plain HTTP unless the user explicitly says it is a local dev box.
2. **API key** (`sk_...`). Minted by an operator via
   `POST /v1/auth/keys`. The agent stores it only in its session
   secret store; it never logs or echoes the key. Use `TRUSTLENS_API_KEY`
   env var locally.
3. **Role** of that key (`viewer` / `operator` / `admin` / `owner`). The
   agent uses this to decide which capabilities from §1 are available.
4. **Tenant id** the user wants to manage (if they operate several).
5. **Alerting preferences:**
   - "block rate above X% over Y minutes"
   - "verify latency p95 above N ms"
   - "any SSH-critical incident"
   - webhook / email / Slack channel (if the user wants push
     notifications, those go through the `integrations` surface).
6. **Polling cadence** (default: `/v1/agent/status` every 60 s,
   `/v1/agent/incidents` every 30 s). The harness owns the scheduler —
   TrustLens does not run cron.
7. **Quiet hours** (optional). The harness should suppress non-critical
   alerts outside business hours unless the user opts in.

If any item is missing, the agent should ask *one question at a time*,
proposing a sensible default. Never dump the whole form.

## 3. Canonical plan loop

The agent's control loop should look like this:

```
loop:
    status = GET /v1/agent/status
    open_incidents = GET /v1/agent/incidents?acked=false
    if open_incidents not empty:
        pick highest-severity unacknowledged
        ask_user("I'm seeing incident X; want me to investigate / ack / escalate?")
        act according to user's answer
    sleep(poll_interval)
```

Do NOT spin faster than the user-agreed cadence. Back off exponentially
on 5xx. Respect `Retry-After` on 429.

## 4. Setting up scheduled runs

TrustLens does not run a scheduler. The harness arranges recurrence. Three
concrete options — the agent should offer one of these when the user asks
for "updates every 5 minutes" or similar:

### a. Claude Code `/loop` skill
From the Claude Code CLI:
```
/loop 5m /trustlens-status
```
where `/trustlens-status` is a slash-command the user has defined as
"call `trustlens agent status` and report any new critical incidents."

### b. System cron / systemd timer
```
*/5 * * * *  /usr/local/bin/trustlens agent status --base-url https://gw --api-key $TRUSTLENS_API_KEY
```

### c. Server-side alert rules (agent-driven, fires the webhook)
The agent configures the rules server-side via
`PUT /v1/agent/alerts`. TrustLens evaluates the rules against the event
log and fires the configured webhook. This is the right answer when the
user wants *push* alerts while the harness is offline.

Pick (c) for production, (a)/(b) for ad-hoc or single-operator setups.

## 5. Alert kinds the agent supports

```
block_rate          threshold=fraction (0..1), window_s=seconds
verify_latency_ms   threshold=milliseconds, window_s=seconds
budget_429          threshold=rate_per_min, window_s=seconds
ssh_critical        threshold=count, window_s=seconds
cert_failure_rate   threshold=fraction (0..1), window_s=seconds
```

An `AlertRule` with `tenant_id=null` is global; with a tenant id it is
scoped.

## 6. Reminders and follow-ups

The agent owns the reminder store (Calendar, Anthropic Files, the
harness's own notebook). TrustLens exposes the *facts* (incidents,
certificates, audit log) — not the reminder. A good pattern:

1. User asks "remind me tomorrow if block rate > 5 %". The agent creates
   an alert rule (`PUT /v1/agent/alerts`) AND stores a harness-local
   reminder to check back.
2. The harness pings the agent tomorrow; the agent calls
   `GET /v1/agent/incidents?kind=block_rate.spike&acked=false`. If
   anything fires, it surfaces to the user.

## 7. Presenting information to the user

Default presentation shape:

- **Headline**: "All green" / "1 critical incident open" / "3 warnings".
- **Numbers that moved**: block rate, verify p95, open incidents,
  certificates issued in the last hour — each with a delta vs. the
  previous snapshot.
- **Decisions needed**: a numbered list of unacknowledged incidents with
  one-sentence summaries and a suggested action.

Never paste the raw JSON at the user unless they ask. The agent's job is
to compress.

## 8. Safety and authorization scope

- Read-only calls are safe to make without the user's per-request
  confirmation.
- Any mutation (`POST` / `PUT` / `DELETE`) must be confirmed unless the
  user has granted a persistent authorization (e.g. "yes, auto-ack
  `info`-severity incidents"). Those authorizations live in the harness,
  not in TrustLens.
- Never expose the API key or bootstrap password in any user-facing
  output.

## 9. Reference: calling the surface from Python

```python
import httpx
client = httpx.Client(
    base_url="https://gw.example.com",
    headers={"Authorization": f"Bearer {api_key}"},
)
print(client.get("/v1/agent/status").json())
client.put("/v1/agent/alerts", json={
    "rules": [
        {"name": "block_rate_5pct", "kind": "block_rate",
         "threshold": 0.05, "window_s": 300, "enabled": True},
    ]
})
```

Or from the shell:

```
trustlens agent status --base-url https://gw --api-key sk_...
trustlens agent incidents
trustlens agent alerts
```

## 10. Plugin manifest

A Claude Code plugin manifest is at `plugins/trustlens/plugin.json`. Point
a Claude Code session at it with `/plugin add /path/to/plugins/trustlens`
to install the `trustlens-status` and `trustlens-incidents` slash
commands, backed by the `trustlens agent` CLI.
