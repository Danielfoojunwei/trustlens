---
name: trustlens-setup
description: Install, configure, and verify a fresh TrustLens deployment end-to-end. Use this whenever the user asks to "set up TrustLens", "install TrustLens", "wire TrustLens to my LLM", or wants to bring a tenant from zero to first verified response.
---

# TrustLens setup wizard

You are walking the user through bringing a TrustLens deployment from
zero to first verified response. Be **proactive**: ask the user only the
questions that the live system can't answer for itself.

## Step 0 — Decide if the gateway is running

Call `trustlens_version()`. Three outcomes:

- `status: "ok"` → gateway is reachable. Skip to Step 1.
- `status: "gateway_down"` → tell the user to run
  `trustlens serve-gateway` in another terminal, then retry.
- Connection error → ask the user where the gateway is hosted (URL),
  then re-instantiate the MCP server with `--gateway-url ...`.

## Step 1 — Authenticate

Call `whoami()`. If `authenticated: false`:

> "I need to sign in to your TrustLens gateway to set things up.
> Default dev account is **owner@trustlens.local / trustlens**.
> Want me to use that, or do you have a real owner account?"

Then `login(email, password)`. Confirm role = owner before continuing.

## Step 2 — Drive the rest from `setup_status()`

Call `setup_status()`. It returns a `next_action` field describing the
single highest-priority gap. **Do not blindly execute it** — read it
aloud to the user, then ask their choice. For example:

- `next_action: "Use kb_upsert to load your KB documents"` →
  > "Your KB is empty. I can either (a) load a tiny demo KB so we can
  > smoke-test, or (b) help you bulk-load your real corpus from a file
  > or URL. Which would you like?"

  - For (a): call `quick_start_demo()` and show the resulting cert.
  - For (b): ask for the source, parse documents into the
    `[{doc_id, text, source_uri?}]` shape, then `kb_upsert(...)`.

- `next_action: "Set the tenant compliance profile"` →
  Ask for **legal_name**, **dpo_contact**, **purposes_of_processing**,
  **deployment_geographies**, **applicable_frameworks** (default
  `["gdpr","iso_27001","iso_42001","nist_ai_rmf","soc_2"]`). Then call
  `profile_update(...)`.

- `next_action: "Seed default AI risks"` → call `risks_seed()`.
- `next_action: "Seed default retention"` → call `retention_seed(tenant_id)`.

After each step, re-call `setup_status()` and report what's now ✓.

## Step 3 — Smoke test

When `setup_status()` shows everything green, call `quick_start_demo()`
and surface the certificate id + status to the user. Tell them how to
verify offline:

```bash
trustlens verify ./.trustlens/certs/<...>.json --public-key ./.trustlens/signer.pub.pem
```

## Step 4 — Hand off

- Show `compliance_overview()` — they should see ~13 frameworks scored.
- Suggest the next skill: `trustlens-kb` for production-scale KB load,
  `trustlens-deep-inspector` to enable the DEEP tier, or
  `trustlens-compliance` to walk a specific regulation.

## Safety reminders

- Default dev passwords (trustlens / operator / viewer) are **only for
  dev**. If you detect a production-looking environment (real domain,
  real KB content), refuse to seed the dev users and ask the user to
  configure proper accounts via the dashboard or env vars.
- Never write a private key path the user didn't authorize.
- Never pass real secrets in plain text in chat — store via the API and
  reference by ID.
