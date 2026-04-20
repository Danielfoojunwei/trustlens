"""`trustlens` CLI — operator tooling.

Subcommands:
    keygen           — generate an Ed25519 signer keypair
    verify <path>    — offline-verify a certificate file
    inspect <path>   — pretty-print a certificate
    serve-verifier   — start the verifier service
    serve-gateway    — start the gateway (needs backend config)
    version          — print versions
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from trustlens.certificate.schema import Certificate
from trustlens.certificate.signer import KeyPair, load_public_key_pem, verify_certificate
from trustlens.version import __version__, PIPELINE_VERSION, CERT_SCHEMA_VERSION


def _cmd_version(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            {
                "trustlens": __version__,
                "pipeline_version": PIPELINE_VERSION,
                "cert_schema_version": CERT_SCHEMA_VERSION,
            },
            indent=2,
        )
    )
    return 0


def _cmd_keygen(args: argparse.Namespace) -> int:
    out_private = Path(args.out)
    if out_private.exists() and not args.force:
        print(f"refusing to overwrite {out_private} (use --force)", file=sys.stderr)
        return 2
    keypair = KeyPair.generate()
    out_private.parent.mkdir(parents=True, exist_ok=True)
    out_private.write_bytes(keypair.private_pem())
    pub_path = out_private.with_suffix(".pub.pem")
    pub_path.write_bytes(keypair.public_pem())
    print(json.dumps({
        "key_id": keypair.key_id,
        "private_key_path": str(out_private),
        "public_key_path": str(pub_path),
    }, indent=2))
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.cert_path)
    cert = Certificate.model_validate_json(path.read_bytes())
    # Emit a readable summary
    claims = cert.payload.claims
    summary = {
        "cert_id": cert.cert_id,
        "signer_key_id": cert.signer_key_id,
        "tenant_id": cert.payload.tenant_id,
        "model_id": cert.payload.model_id,
        "overall_status": cert.payload.overall_status.value,
        "pipeline_version": cert.payload.pipeline_version,
        "schema_version": cert.payload.schema_version,
        "num_claims": len(claims),
        "renderable_claims": sum(1 for c in claims if c.is_renderable),
        "verdict_breakdown": _verdict_breakdown(claims),
        "oracles_used": cert.payload.oracles_used,
        "degradations": cert.payload.degradations,
        "issued_at": cert.payload.issued_at,
    }
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    pub_pem = Path(args.public_key).read_bytes()
    pub = load_public_key_pem(pub_pem)

    trusted = None
    if args.trusted_key_ids:
        trusted = {s.strip() for s in args.trusted_key_ids.split(",") if s.strip()}

    cert = Certificate.model_validate_json(Path(args.cert_path).read_bytes())
    result = verify_certificate(
        cert, pub,
        require_pipeline_version=args.require_pipeline_version,
        require_schema_version=args.require_schema_version,
        trusted_key_ids=trusted,
    )

    out = {
        "valid": result.valid,
        "reason": result.reason,
        "pipeline_version_match": result.pipeline_version_match,
        "schema_version_match": result.schema_version_match,
        "cert_id": cert.cert_id,
        "signer_key_id": cert.signer_key_id,
        "overall_status": cert.payload.overall_status.value,
    }
    print(json.dumps(out, indent=2))
    return 0 if result.valid else 1


def _assert_tls_or_dev(args: argparse.Namespace) -> None:
    """Refuse HTTP when prod mode is set and no TLS flags / reverse proxy."""
    prod = os.environ.get("TRUSTLENS_PROD_MODE", "").strip() == "1"
    behind_proxy = os.environ.get("TRUSTLENS_BEHIND_TLS_PROXY", "").strip() == "1"
    has_tls = bool(getattr(args, "ssl_keyfile", None) and getattr(args, "ssl_certfile", None))
    if prod and not has_tls and not behind_proxy:
        print(
            "TRUSTLENS_PROD_MODE=1 refuses to start without TLS. "
            "Provide --ssl-keyfile/--ssl-certfile, or set "
            "TRUSTLENS_BEHIND_TLS_PROXY=1 if a TLS-terminating proxy is in front.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _uvicorn_kwargs(args: argparse.Namespace) -> dict:
    kw: dict = {"host": args.host, "port": args.port}
    if getattr(args, "ssl_keyfile", None):
        kw["ssl_keyfile"] = args.ssl_keyfile
    if getattr(args, "ssl_certfile", None):
        kw["ssl_certfile"] = args.ssl_certfile
    return kw


def _cmd_serve_verifier(args: argparse.Namespace) -> int:
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("uvicorn not installed", file=sys.stderr)
        return 2
    _assert_tls_or_dev(args)
    os.environ["TRUSTLENS_AUTOSTART"] = "1"
    from trustlens.verifier.service import _default_app  # type: ignore[attr-defined]
    import uvicorn
    uvicorn.run(_default_app(), **_uvicorn_kwargs(args))
    return 0


def _cmd_serve_gateway(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed", file=sys.stderr)
        return 2
    from trustlens.gateway.app import build_gateway
    from trustlens.gateway.backends import BackendRegistry, EchoBackend, OpenAICompatBackend
    from trustlens.oracles.registry import OracleRegistry
    from trustlens.oracles.customer_kb import CustomerKBOracle, LexicalKBIndex
    from trustlens.verifier.engine import VerifierEngine
    from trustlens.tenancy.config import InMemoryTenantStore, TenantConfig, TenantTier
    from trustlens.certificate.store import FilesystemStore

    key_path = Path(args.signer_key)
    if key_path.exists():
        keypair = KeyPair.from_private_pem(key_path.read_bytes())
    else:
        keypair = KeyPair.generate()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(keypair.private_pem())
    # Always (re-)write the public key next to the private key so operators
    # have something to hand to auditors without running `trustlens keygen`.
    pub_path = key_path.with_suffix(".pub.pem")
    if not pub_path.exists():
        pub_path.write_bytes(keypair.public_pem())

    cert_store = FilesystemStore(args.cert_store)

    # Determine which backends to register based on env vars / flags
    backends = [EchoBackend()]
    allowed_backends = ["echo"]

    # OpenAI-compatible (OpenAI, vLLM, Together, etc.)
    openai_key = os.environ.get("OPENAI_API_KEY")
    backend_url = os.environ.get("TRUSTLENS_BACKEND_URL")
    if backend_url:
        backends.append(OpenAICompatBackend(
            name="openai",
            base_url=backend_url,
            api_key=openai_key,
        ))
        allowed_backends.append("openai")

    # Anthropic backend
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            from trustlens.gateway.backends_anthropic import AnthropicBackend
            backends.append(AnthropicBackend(api_key=anthropic_key))
            allowed_backends.append("anthropic")
        except ImportError:
            print("anthropic SDK not installed — Anthropic backend disabled", file=sys.stderr)

    # Ollama backend (local, no key needed)
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "")
    if ollama_url:
        try:
            from trustlens.gateway.backends_ollama import OllamaBackend
            backends.append(OllamaBackend(base_url=ollama_url))
            allowed_backends.append("ollama")
        except ImportError:
            print("httpx not installed — Ollama backend disabled", file=sys.stderr)

    # Default demo config: one tenant 'demo', registered backends, in-memory KB
    tenant_store = InMemoryTenantStore([
        TenantConfig(tenant_id="demo", tier=TenantTier.PRO,
                     allowed_backends=allowed_backends),
    ])
    backend_registry = BackendRegistry(backends)
    kb_index = LexicalKBIndex()
    registry = OracleRegistry([CustomerKBOracle(kb_index)])
    engine = VerifierEngine(registry)

    app = build_gateway(
        engine=engine,
        signer=keypair,
        cert_store=cert_store,
        backend_registry=backend_registry,
        tenant_store=tenant_store,
        kb_index=kb_index,
    )
    _assert_tls_or_dev(args)
    active = ", ".join(b.name for b in backends)
    print(json.dumps({"status": "starting", "backends": active,
                      "host": args.host, "port": args.port,
                      "tls": bool(getattr(args, "ssl_keyfile", None))}))
    uvicorn.run(app, **_uvicorn_kwargs(args))
    return 0


# ---------------------------------------------------------------------------
# Diagnostic / benchmark subcommands
# ---------------------------------------------------------------------------

def _cmd_calibrate(args: argparse.Namespace) -> int:
    """Run Platt scaling calibration on a labeled JSONL dataset.

    Each line of the dataset: {"prompt": "...", "response": "...", "label": 1/0}
    where label=1 means 'correct' and label=0 means 'incorrect/hallucinated'.
    """
    from trustlens.verifier.calibration import calibrate
    import json as _json

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"file not found: {data_path}", file=sys.stderr)
        return 2

    scores: list[float] = []
    labels: list[int] = []
    with data_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = _json.loads(line)
            scores.append(float(item["score"]))
            labels.append(int(item["label"]))

    if len(scores) < 5:
        print("need at least 5 labeled items for calibration", file=sys.stderr)
        return 2

    report = calibrate(scores, labels)
    _json.dump(report.to_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _cmd_attribution(args: argparse.Namespace) -> int:
    """Run per-component failure attribution on the bundled HALU_EVAL corpus."""
    import asyncio
    import json as _json
    from trustlens.deep_inspector.benchmarks.failure_attribution import run_attribution
    result = asyncio.run(run_attribution())
    _json.dump(result.to_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    """Run the 10-axis capability sweep (requires HF datasets + evaluate)."""
    import asyncio
    import json as _json
    from trustlens.deep_inspector.benchmarks.capability_axes import run_capability_sweep
    result = asyncio.run(run_capability_sweep(n_samples=args.n_samples))
    _json.dump(result.to_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _agent_client(base_url: str, api_key: Optional[str]) -> "httpx.Client":
    import httpx  # type: ignore
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=10.0)


def _cmd_agent(args: argparse.Namespace) -> int:
    """Call an agent control endpoint.

    Example:
        trustlens agent status --base-url https://gw.example.com --api-key sk_...
    """
    import json as _json
    api_key = args.api_key or os.environ.get("TRUSTLENS_API_KEY")
    client = _agent_client(args.base_url, api_key)
    path = {
        "status":       ("GET",  "/v1/agent/status"),
        "capabilities": ("GET",  "/v1/agent/capabilities"),
        "tenants":      ("GET",  "/v1/agent/tenants"),
        "incidents":    ("GET",  "/v1/agent/incidents"),
        "alerts":       ("GET",  "/v1/agent/alerts"),
    }.get(args.action)
    if path is None:
        print(f"unknown action: {args.action}", file=sys.stderr)
        return 2
    method, url = path
    r = client.request(method, url)
    try:
        body = r.json()
    except ValueError:
        body = {"status_code": r.status_code, "text": r.text[:1000]}
    _json.dump(body, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0 if r.status_code < 400 else 1


# ---------------------------------------------------------------------------
# Agentic commands
# ---------------------------------------------------------------------------

def _cmd_mcp_serve(args: argparse.Namespace) -> int:
    """Run the MCP server. Default transport is stdio (Claude Desktop /
    Code wires it up natively). Use sse/streamable-http for remote runs."""
    try:
        from trustlens.mcp.server import build_server
    except ImportError as e:
        print(f"MCP SDK not installed: {e}", file=sys.stderr)
        print("→ pip install mcp", file=sys.stderr)
        return 2
    srv = build_server(
        base_url=args.gateway_url, api_key=args.api_key,
        tenant_id=args.tenant_id,
    )
    if args.transport == "stdio":
        srv.run()                         # FastMCP defaults to stdio
    elif args.transport == "sse":
        srv.run(transport="sse")
    else:
        srv.run(transport="streamable-http")
    return 0


def _cmd_mcp_tools(args: argparse.Namespace) -> int:
    """Print every MCP tool + a one-line description as JSON. Useful for
    'what can I do via the MCP surface?' from any agent or shell."""
    try:
        from trustlens.mcp.server import build_server
    except ImportError as e:
        print(f"MCP SDK not installed: {e}", file=sys.stderr)
        return 2
    srv = build_server()
    import asyncio, json as _json, inspect
    async def _enum() -> list[dict]:
        # FastMCP exposes the tools via list_tools() on the underlying server
        items = await srv.list_tools()
        out = []
        for t in items:
            doc = (t.description or "").strip()
            first_line = doc.splitlines()[0] if doc else ""
            out.append({"name": t.name, "summary": first_line})
        return out
    tools = asyncio.run(_enum())
    print(_json.dumps({"n_tools": len(tools), "tools": tools}, indent=2))
    return 0


def _cmd_setup(args: argparse.Namespace) -> int:
    """Interactive setup wizard. With --json, prints a setup status JSON
    suitable for an agent to drive the conversation."""
    import asyncio, json as _json
    from trustlens.mcp.client import GatewayClient

    async def _go() -> dict:
        c = GatewayClient(base_url=args.gateway_url)
        # 1. health
        try:
            health = await c.get("/healthz")
        except Exception as e:
            return {"status": "gateway_down",
                    "next_action": "Run `trustlens serve-gateway` first.",
                    "error": str(e), "base_url": c.base_url}
        # 2. login as default owner
        try:
            await c.login("owner@trustlens.local", "trustlens")
        except Exception as e:
            return {"status": "login_failed", "error": str(e)}
        # 3. setup_status equivalent
        out = {"status": "ok",
                "base_url": c.base_url, "gateway": health,
                "checks": {}}
        try: out["checks"]["kb_size"] = (await c.get(
            f"/v1/kb/status?tenant_id={c.tenant_id}")).get("index_size", 0)
        except Exception: out["checks"]["kb_size"] = 0
        try:
            prof = await c.get(f"/v1/admin/compliance/profile/{c.tenant_id}")
            out["checks"]["legal_name_set"] = bool(prof.get("legal_name"))
            out["checks"]["dpo_contact_set"] = bool(prof.get("dpo_contact"))
        except Exception:
            out["checks"]["legal_name_set"] = False
            out["checks"]["dpo_contact_set"] = False
        try: out["checks"]["risks_seeded"] = len(await c.get(
            "/v1/admin/compliance/risks")) >= 6
        except Exception: out["checks"]["risks_seeded"] = False
        try: out["checks"]["retention_seeded"] = len(await c.get(
            "/v1/admin/compliance/retention")) >= 6
        except Exception: out["checks"]["retention_seeded"] = False

        # Recommend next action
        ck = out["checks"]
        if ck["kb_size"] == 0:
            out["next_action"] = "Load KB documents via `POST /v1/admin/kb/upsert` or the dashboard."
        elif not ck["legal_name_set"]:
            out["next_action"] = "Set the tenant compliance profile (legal_name, DPO contact)."
        elif not ck["risks_seeded"]:
            out["next_action"] = "Seed default AI risks: `POST /v1/admin/compliance/risks/seed`."
        elif not ck["retention_seeded"]:
            out["next_action"] = "Seed default retention policies."
        else:
            out["next_action"] = "Setup complete."
        await c.close()
        return out

    result = asyncio.run(_go())
    if args.json:
        _json.dump(result, sys.stdout, indent=2); sys.stdout.write("\n")
        return 0 if result.get("status") == "ok" else 1

    # Interactive output
    print(f"\n  TrustLens setup status — gateway @ {result.get('base_url')}\n")
    if result.get("status") != "ok":
        print(f"  ✗ {result.get('status')}")
        print(f"  → {result.get('next_action', 'investigate the error')}")
        if "error" in result:
            print(f"    error: {result['error']}")
        return 1
    for k, v in result["checks"].items():
        sym = "✓" if v else "○"
        print(f"  {sym}  {k:<28} = {v}")
    print(f"\n  → next action: {result['next_action']}\n")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Print a structured diagnostic JSON of the running gateway."""
    import asyncio, json as _json
    from trustlens.mcp.client import GatewayClient
    async def _go() -> dict:
        c = GatewayClient(base_url=args.gateway_url)
        out: dict = {"base_url": c.base_url, "tenant_id": c.tenant_id}
        for path, key in [
            ("/healthz",                              "health"),
            ("/readyz",                               "ready"),
            ("/openapi.json",                         "_openapi"),
            ("/v1/admin/compliance/audit-log/verify", "audit_chain"),
            ("/v1/admin/compliance/overview",         "compliance"),
        ]:
            try:
                out[key] = await c.get(path)
            except Exception as e:
                out[key] = {"error": str(e)}
        # Trim openapi for readability
        if isinstance(out.get("_openapi"), dict):
            out["openapi_paths"] = sorted((out["_openapi"].get("paths") or {}).keys())
            out.pop("_openapi", None)
        await c.close()
        return out
    result = asyncio.run(_go())
    _json.dump(result, sys.stdout, indent=2); sys.stdout.write("\n")
    chain_ok = (isinstance(result.get("audit_chain"), dict)
                and result["audit_chain"].get("ok") is True)
    return 0 if chain_ok else 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verdict_breakdown(claims) -> dict:
    out: dict[str, int] = {}
    for c in claims:
        key = c.verdict.value if hasattr(c.verdict, "value") else str(c.verdict)
        out[key] = out.get(key, 0) + 1
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trustlens")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version").set_defaults(func=_cmd_version)

    k = sub.add_parser("keygen", help="generate an Ed25519 signer keypair")
    k.add_argument("--out", required=True, help="path to write private key PEM")
    k.add_argument("--force", action="store_true")
    k.set_defaults(func=_cmd_keygen)

    v = sub.add_parser("verify", help="offline verify a certificate")
    v.add_argument("cert_path")
    v.add_argument("--public-key", required=True)
    v.add_argument("--require-pipeline-version", default=None)
    v.add_argument("--require-schema-version", default=None)
    v.add_argument("--trusted-key-ids", default=None,
                   help="comma-separated list of key-ids to accept")
    v.set_defaults(func=_cmd_verify)

    i = sub.add_parser("inspect", help="pretty-print a certificate")
    i.add_argument("cert_path")
    i.set_defaults(func=_cmd_inspect)

    sv = sub.add_parser("serve-verifier", help="start the verifier service")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--ssl-keyfile", default=None, help="path to TLS private key PEM")
    sv.add_argument("--ssl-certfile", default=None, help="path to TLS cert PEM")
    sv.set_defaults(func=_cmd_serve_verifier)

    sg = sub.add_parser("serve-gateway", help="start a demo gateway")
    sg.add_argument("--host", default="0.0.0.0")
    sg.add_argument("--port", type=int, default=8081)
    sg.add_argument("--signer-key", default="./.trustlens/signer.pem")
    sg.add_argument("--cert-store", default="./.trustlens/certs")
    sg.add_argument("--ssl-keyfile", default=None, help="path to TLS private key PEM")
    sg.add_argument("--ssl-certfile", default=None, help="path to TLS cert PEM")
    sg.set_defaults(func=_cmd_serve_gateway)

    cal = sub.add_parser("calibrate",
                         help="run Platt scaling calibration on labeled NLI scores")
    cal.add_argument("data", help="JSONL file with {score, label} lines")
    cal.set_defaults(func=_cmd_calibrate)

    attr = sub.add_parser("attribution",
                          help="per-component failure attribution on HALU_EVAL corpus")
    attr.set_defaults(func=_cmd_attribution)

    sw = sub.add_parser("sweep",
                        help="10-axis capability sweep (requires HF datasets)")
    sw.add_argument("--n-samples", type=int, default=20,
                    help="samples per axis per alpha point")
    sw.set_defaults(func=_cmd_sweep)

    # ---------- agentic commands ----------
    mcp_p = sub.add_parser("mcp",
                            help="Model Context Protocol server (agent control plane)")
    mcp_sub = mcp_p.add_subparsers(dest="mcp_cmd", required=True)
    mcp_serve = mcp_sub.add_parser("serve", help="serve the MCP tool surface")
    mcp_serve.add_argument("--transport", default="stdio",
                            choices=["stdio", "sse", "streamable-http"],
                            help="transport — stdio for Claude Desktop / Code, sse/http for remote")
    mcp_serve.add_argument("--port", type=int, default=8090)
    mcp_serve.add_argument("--gateway-url", default=None,
                            help="base URL of the TrustLens gateway "
                                 "(default: $TRUSTLENS_BASE_URL or http://127.0.0.1:8081)")
    mcp_serve.add_argument("--api-key", default=None,
                            help="bearer token for the gateway "
                                 "(default: $TRUSTLENS_API_KEY)")
    mcp_serve.add_argument("--tenant-id", default=None,
                            help="default tenant header (default: $TRUSTLENS_TENANT_ID or 'demo')")
    mcp_serve.set_defaults(func=_cmd_mcp_serve)

    mcp_tools = mcp_sub.add_parser("tools",
                                    help="list every tool the MCP server exposes")
    mcp_tools.set_defaults(func=_cmd_mcp_tools)

    setup = sub.add_parser("setup",
                            help="interactive setup wizard (works with --json for agents)")
    setup.add_argument("--gateway-url", default=None)
    setup.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON instead of asking interactively")
    setup.set_defaults(func=_cmd_setup)

    doctor = sub.add_parser("doctor",
                             help="diagnose the running gateway + suggest next steps")
    doctor.add_argument("--gateway-url", default=None)
    doctor.set_defaults(func=_cmd_doctor)

    ag = sub.add_parser("agent",
                        help="agent control surface (status, tenants, incidents, alerts)")
    ag.add_argument("action",
                    choices=["status", "capabilities", "tenants",
                             "incidents", "alerts"])
    ag.add_argument("--base-url", default="http://127.0.0.1:8081",
                    help="gateway base URL")
    ag.add_argument("--api-key", default=None,
                    help="Bearer API key (defaults to $TRUSTLENS_API_KEY)")
    ag.set_defaults(func=_cmd_agent)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
