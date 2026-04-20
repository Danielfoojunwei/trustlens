"""TrustLens MCP server — exposes the entire control surface as
agent-callable tools so any LLM running in any MCP-compatible harness
(Claude Code, Claude Desktop, Cursor, Continue, ...) can install,
configure, operate, audit, and tune TrustLens end-to-end.

Run via the CLI:
    trustlens mcp serve --transport stdio
    trustlens mcp serve --transport sse --port 8090

Or programmatically:
    from trustlens.mcp.server import build_server
    server = build_server()
    server.run()
"""
from trustlens.mcp.server import TRUSTLENS_TOOL_VERSION, build_server

__all__ = ["TRUSTLENS_TOOL_VERSION", "build_server"]
