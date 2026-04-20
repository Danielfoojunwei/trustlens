"""Tiny HTTP client used by the MCP server to talk to a running gateway.

Wraps the admin REST surface so each MCP tool is a thin call. The agent
runtime (Claude / Cursor / etc.) carries the authority — this module only
forwards calls + returns structured JSON.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx


DEFAULT_BASE_URL = os.environ.get("TRUSTLENS_BASE_URL", "http://127.0.0.1:8081")
DEFAULT_API_KEY  = os.environ.get("TRUSTLENS_API_KEY", "")
DEFAULT_TENANT   = os.environ.get("TRUSTLENS_TENANT_ID", "demo")


class GatewayClient:
    """Authenticated wrapper around the gateway's REST surface."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key   = api_key   or DEFAULT_API_KEY
        self.tenant_id = tenant_id or DEFAULT_TENANT
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._cookies = httpx.Cookies()

    # ----------------------------------------------------------------
    async def login(self, email: str, password: str) -> dict:
        r = await self._client.post(
            f"{self.base_url}/v1/auth/login",
            json={"email": email, "password": password},
            cookies=self._cookies,
        )
        for c in r.cookies.jar:
            self._cookies.set(c.name, c.value)
        r.raise_for_status()
        return r.json()

    async def whoami(self) -> dict:
        r = await self._call("GET", "/v1/auth/me")
        return r

    async def close(self) -> None:
        await self._client.aclose()

    # ----------------------------------------------------------------
    def _headers(self) -> dict:
        h: dict[str, str] = {"X-TrustLens-Tenant-Id": self.tenant_id}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def _call(self, method: str, path: str, **kw) -> Any:
        url = f"{self.base_url}{path}"
        kw.setdefault("headers", {}).update(self._headers())
        kw.setdefault("cookies", self._cookies)
        r = await self._client.request(method, url, **kw)
        if r.status_code >= 400:
            try:
                body = r.json()
            except Exception:
                body = {"raw": r.text[:400]}
            raise RuntimeError(f"HTTP {r.status_code} {method} {path}: {body}")
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.text

    # ----------------------------------------------------------------
    # Thin convenience methods used by the MCP tools
    # ----------------------------------------------------------------
    async def get(self, path: str, **kw) -> Any:
        return await self._call("GET", path, **kw)

    async def post(self, path: str, body: Optional[dict] = None, **kw) -> Any:
        if body is not None:
            kw["json"] = body
        return await self._call("POST", path, **kw)

    async def put(self, path: str, body: Optional[dict] = None, **kw) -> Any:
        if body is not None:
            kw["json"] = body
        return await self._call("PUT", path, **kw)

    async def patch(self, path: str, body: Optional[dict] = None, **kw) -> Any:
        if body is not None:
            kw["json"] = body
        return await self._call("PATCH", path, **kw)

    async def delete(self, path: str, **kw) -> Any:
        return await self._call("DELETE", path, **kw)
