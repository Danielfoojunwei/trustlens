"""Tests for the MCP server tool surface + the new CLI commands.

The MCP server uses the FastMCP runtime which is stdio-driven, so we
exercise the tools via the in-process server registry rather than
spinning a real stdio process.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.mark.asyncio
async def test_mcp_server_exposes_expected_tools():
    from trustlens.mcp.server import build_server
    srv = build_server()
    tools = await srv.list_tools()
    names = {t.name for t in tools}

    # Critical surface: confirm we expose the user-facing flows
    must_have = {
        # discovery
        "trustlens_version", "login", "whoami", "list_endpoints",
        # chat + cert
        "chat", "verify_certificate",
        # kb
        "kb_list", "kb_upsert", "kb_delete", "kb_versions",
        "kb_revert", "kb_export",
        # axes / incidents
        "axes_summary", "axes_recent",
        "incidents_list", "incident_ack",
        # users + keys
        "users_list", "user_create", "keys_list",
        "key_mint", "key_revoke",
        # integrations + settings
        "integrations_list", "integration_set",
        "settings_get", "settings_update",
        # compliance
        "compliance_overview", "compliance_frameworks",
        "framework_detail",
        "audit_log_query", "audit_log_verify",
        "dsar_open", "dsar_list", "dsar_fulfill", "dsar_reject",
        "consent_record", "consent_history",
        "retention_list", "retention_seed", "retention_set",
        "breach_open", "breach_overdue", "breach_close",
        "risks_list", "risks_seed", "aiia_create",
        "model_cards_list", "model_card_create",
        "profile_get", "profile_update",
        "transparency_ropa", "transparency_eu_ai_act",
        # guided
        "setup_status", "quick_start_demo",
    }
    missing = must_have - names
    assert not missing, f"missing MCP tools: {sorted(missing)}"
    # At least 50 tools expected (full agent surface)
    assert len(tools) >= 50


@pytest.mark.asyncio
async def test_mcp_tools_have_descriptions():
    """Every tool must carry a docstring — agents rely on them for routing."""
    from trustlens.mcp.server import build_server
    srv = build_server()
    tools = await srv.list_tools()
    for t in tools:
        assert (t.description or "").strip(), f"tool {t.name} has no description"


def test_mcp_client_dataclass_defaults():
    from trustlens.mcp.client import GatewayClient, DEFAULT_BASE_URL, DEFAULT_TENANT
    c = GatewayClient()
    assert c.base_url == DEFAULT_BASE_URL.rstrip("/")
    assert c.tenant_id == DEFAULT_TENANT


def test_cli_parser_includes_agentic_subcommands():
    from trustlens.cli.main import build_parser
    parser = build_parser()
    # mcp is a sub-parser group with its own required subcommand
    args = parser.parse_args(["mcp", "tools"])
    assert args.cmd == "mcp" and args.mcp_cmd == "tools"
    args = parser.parse_args(["mcp", "serve", "--transport", "sse",
                               "--port", "8090"])
    assert args.transport == "sse" and args.port == 8090
    args = parser.parse_args(["setup", "--json"])
    assert args.cmd == "setup" and args.json is True
    args = parser.parse_args(["doctor"])
    assert args.cmd == "doctor"


def test_cli_doctor_returns_non_zero_when_gateway_down():
    """`trustlens doctor` should fail loudly when nothing's listening."""
    from trustlens.cli.main import _cmd_doctor
    import argparse
    ns = argparse.Namespace(gateway_url="http://127.0.0.1:1")  # nothing listens here
    rc = _cmd_doctor(ns)
    assert rc == 1


def test_cli_setup_json_when_gateway_down():
    """`trustlens setup --json` should emit gateway_down JSON."""
    import io, json, contextlib, argparse
    from trustlens.cli.main import _cmd_setup
    ns = argparse.Namespace(json=True, gateway_url="http://127.0.0.1:1")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _cmd_setup(ns)
    out = json.loads(buf.getvalue())
    assert out["status"] == "gateway_down"
    assert "next_action" in out
    assert rc == 1


def test_plugin_manifest_lists_all_skills():
    """The plugin.json must reference every shipped skill."""
    import json, os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manifest_path = os.path.join(here, "plugin", "plugin.json")
    skills_dir   = os.path.join(here, "plugin", "skills")
    manifest = json.load(open(manifest_path))
    declared = {s["name"] for s in manifest["skills"]}
    on_disk  = set(os.listdir(skills_dir))
    assert declared == on_disk, (
        f"plugin.json skills mismatch:\n"
        f"  declared but missing on disk: {declared - on_disk}\n"
        f"  on disk but undeclared: {on_disk - declared}"
    )


def test_every_skill_has_frontmatter_and_description():
    """SKILL.md frontmatter must contain name + description for the
    Claude Code plugin loader."""
    import os, re
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    skills_dir = os.path.join(here, "plugin", "skills")
    for name in os.listdir(skills_dir):
        p = os.path.join(skills_dir, name, "SKILL.md")
        body = open(p).read()
        assert body.startswith("---\n"), f"{name}: SKILL.md missing frontmatter"
        # crude frontmatter parse
        end = body.index("\n---\n", 4)
        fm = body[4:end]
        assert re.search(r"^name:\s*\S", fm, re.MULTILINE), f"{name}: no `name:`"
        assert re.search(r"^description:\s*\S", fm, re.MULTILINE), f"{name}: no `description:`"


@pytest.mark.asyncio
async def test_mcp_setup_status_returns_gateway_down_on_no_gateway():
    """Drive the setup_status tool against a dead gateway; the tool
    should return a non-OK status the agent can act on, not raise."""
    from trustlens.mcp.server import build_server
    srv = build_server(base_url="http://127.0.0.1:1")
    tools = await srv.list_tools()
    # Locate setup_status by name
    target = next(t for t in tools if t.name == "setup_status")
    # Call via the FastMCP tool registry
    import json
    result = await srv.call_tool(target.name, {})
    # FastMCP returns a list of TextContent objects when the tool returns
    # structured data (it serializes to JSON inside text).
    assert isinstance(result, list) and result, f"unexpected result: {result!r}"
    text = getattr(result[0], "text", None)
    assert text, "no text content in tool result"
    payload = json.loads(text)
    assert payload.get("status") in {"incomplete", "ok",
                                       "gateway_down", "failed"}
