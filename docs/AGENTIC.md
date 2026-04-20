# AGENTIC — TrustLens as an agent control plane

TrustLens ships **four distinct ways** for an LLM agent (Claude, Cursor,
Continue, your own) to drive the entire product end-to-end:

1. **MCP server** — 53 tools across discovery, chat, KB, axes, incidents,
   users, keys, integrations, settings, compliance.
2. **CLI** — every operator action is a command; `--json` flags mean
   any agent can shell out and parse the output.
3. **Claude Code plugin** — `plugin/plugin.json` + 7 `SKILL.md` files
   the harness loads automatically.
4. **REST admin API** — the same endpoints the dashboard uses; an agent
   can hit them directly with an API key.

The design rule is identical across all four surfaces: **the agent
proposes, the user approves**. Destructive operations (KB delete,
breach close, retention change, key revoke) are explicitly tagged in
the tool docstring so the LLM knows to confirm before invoking.

---

## 1. MCP server

### Run

```bash
# stdio (Claude Desktop, Claude Code, Cursor, Continue, ...)
trustlens mcp serve

# remote SSE
trustlens mcp serve --transport sse --port 8090

# explicit upstream gateway
trustlens mcp serve \
  --gateway-url https://trustlens.yourco.net \
  --tenant-id acme
```

### Wire into Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "trustlens": {
      "command": "trustlens",
      "args": ["mcp", "serve", "--transport", "stdio"],
      "env": {
        "TRUSTLENS_BASE_URL":  "http://127.0.0.1:8081",
        "TRUSTLENS_TENANT_ID": "demo"
      }
    }
  }
}
```

### Wire into Cursor / Continue / OpenAI Agents SDK

Same shape — point at `trustlens mcp serve` over stdio (or SSE for a
hosted gateway).

### List the tools

```bash
trustlens mcp tools
```

returns JSON of all 53 tools and their one-line summaries.

### Tool catalog (53 tools)

**Discovery (4):** `trustlens_version`, `login`, `whoami`, `list_endpoints`

**Chat + verification (2):** `chat`, `verify_certificate`

**KB (6):** `kb_list`, `kb_upsert`, `kb_delete`, `kb_versions`,
`kb_revert`, `kb_export`

**Axes / observability (2):** `axes_summary`, `axes_recent`

**Incidents (2):** `incidents_list`, `incident_ack`

**Settings + tier (2):** `settings_get`, `settings_update`

**Users + API keys (5):** `users_list`, `user_create`, `keys_list`,
`key_mint`, `key_revoke`

**Integrations (2):** `integrations_list`, `integration_set`

**Compliance — frameworks (3):** `compliance_overview`,
`compliance_frameworks`, `framework_detail`

**Compliance — audit log (2):** `audit_log_query`, `audit_log_verify`

**Compliance — DSAR (4):** `dsar_open`, `dsar_list`, `dsar_fulfill`,
`dsar_reject`

**Compliance — consent (2):** `consent_record`, `consent_history`

**Compliance — retention (3):** `retention_list`, `retention_seed`,
`retention_set`

**Compliance — breach (3):** `breach_open`, `breach_overdue`,
`breach_close`

**Compliance — risks (3):** `risks_list`, `risks_seed`, `aiia_create`

**Compliance — model cards (2):** `model_cards_list`, `model_card_create`

**Compliance — profile + transparency (4):** `profile_get`,
`profile_update`, `transparency_ropa`, `transparency_eu_ai_act`

**Guided (2):** `setup_status`, `quick_start_demo`

### Resources

| URI | What |
|---|---|
| `trustlens://compliance/overview` | Live compliance overview JSON |
| `trustlens://help/getting-started` | Operator cheatsheet for agents |

---

## 2. CLI

### New agentic subcommands

```bash
trustlens mcp serve [--transport stdio|sse|streamable-http]
trustlens mcp tools                    # JSON list of all MCP tools
trustlens setup [--json] [--gateway-url URL]
trustlens doctor [--gateway-url URL]
```

### Existing operator commands (composable from agents)

```bash
trustlens version
trustlens keygen --out ./.trustlens/signer.pem
trustlens verify ./cert.json --public-key ./signer.pub.pem
trustlens inspect ./cert.json
trustlens serve-verifier
trustlens serve-gateway
trustlens calibrate ./labeled_scores.jsonl
trustlens attribution
trustlens sweep --n-samples 20
```

Every command writes JSON to stdout where applicable, so an agent can
shell out and parse without prompts.

---

## 3. Claude Code plugin

```
plugin/
├── plugin.json
└── skills/
    ├── trustlens-setup/SKILL.md          install + configure end-to-end
    ├── trustlens-verify/SKILL.md         verify a response or cert
    ├── trustlens-kb/SKILL.md             KB CRUD + versioning + export
    ├── trustlens-compliance/SKILL.md     13-framework compliance ops
    ├── trustlens-deep-inspector/SKILL.md SSH + steering tuning
    ├── trustlens-ops/SKILL.md            users, keys, integrations
    └── trustlens-incident/SKILL.md       triage + breach response
```

Install via your harness's plugin loader. The plugin's `mcp_servers`
section automatically launches the MCP server when needed.

### Skill-routing rules (built into each SKILL.md)

| User says… | Skill |
|---|---|
| "Set up TrustLens" / "install" / "wire up my LLM" | trustlens-setup |
| "Is this answer hallucinated?" / "verify the response" | trustlens-verify |
| "Load my docs" / "add to KB" / "export the KB" | trustlens-kb |
| "Are we GDPR-compliant?" / "open a DSAR" / "report a breach" | trustlens-compliance |
| "Enable Deep Inspector" / "tune SSH thresholds" | trustlens-deep-inspector |
| "Add an admin user" / "rotate API key" / "wire Slack alerts" | trustlens-ops |
| "What's wrong with the gateway?" / "ack this alert" | trustlens-incident |

---

## 4. REST admin API (direct)

If you don't want to run the MCP server, every tool above maps 1:1 to a
REST endpoint under `/v1/admin/*`. Get the full live OpenAPI:

```bash
curl http://127.0.0.1:8081/openapi.json
```

Authenticate with either:

- **API key** in `Authorization: Bearer sk_...` (mint via `key_mint` or
  `POST /v1/auth/keys`)
- **Session cookie** from `POST /v1/auth/login`

Every privileged endpoint is RBAC-gated — see
`trustlens/auth/rbac.py` for the role → permission matrix.

---

## Agent safety contract

Every TrustLens MCP tool docstring labels its blast radius. The plugin
manifest tags the destructive ones explicitly:

```json
"agent_safety": {
  "destructive_tools": [
    "kb_delete", "kb_revert", "key_revoke", "user_delete",
    "breach_close", "dsar_reject", "retention_set"
  ],
  "always_confirm_with_user": true
}
```

Conforming agents (Claude with default safety prompts, Cursor with
"safe mode") will pause for user confirmation before invoking any of
these.

---

## End-to-end example: agent driving setup from zero

```
USER: install trustlens for my org
AGENT: [calls trustlens_version()]
       Gateway is reachable at http://127.0.0.1:8081, version 1.0.0.
       I'll need to sign in. May I use the dev owner account
       (owner@trustlens.local / trustlens), or do you have your own?

USER: use the dev one for now
AGENT: [calls login("owner@trustlens.local", "trustlens")]
       [calls setup_status()]
       Here's what's missing:
         ✗ kb_size=0
         ✗ legal_name not set
         ✗ risks not seeded
         ✗ retention not seeded
       Where would you like to start? Loading your KB, configuring
       the compliance profile, or seeding the defaults?

USER: seed the defaults, then we'll add KB
AGENT: [calls risks_seed(), retention_seed("demo")]
       Done. 6 default AI risks added (hallucination, prompt injection,
       PII leak, discrimination, automation bias, opacity), and 7
       retention policies (certs 7y, audit log 7y, chat logs 180d
       w/ anonymize, KB 3y, incidents 2y, bench 1y, profiles 1y).

       For the KB — do you have a JSONL file ready, a directory of
       text files, or should we start with a tiny demo KB?
...
```

The agent stays in conversation, the user stays in control, and every
mutation lands in the SHA-256 hash-chained audit log for offline
verification later.
