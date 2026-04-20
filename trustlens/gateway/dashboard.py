"""TrustLens operator dashboard.

Single-file HTML + vanilla JS. Zero build step. Mounted at ``/dashboard``
by ``build_gateway``.

What the dashboard does:
    - Overview: health, readiness, pipeline version, Prometheus snapshot
    - KB Management: POST /v1/kb/load and GET /v1/kb/status
    - Playground: send chat completions with per-request tau/tau_prime/tier
      and render the certificate inline
    - Certificates: paste or look up a cert, see claims/verdicts/receipts
    - Tuning: sweep tau values against the same prompt, see verdicts move

All UI state is client-side. All data comes from the existing JSON APIs
the gateway already exposes — no new server surface.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

_HERE = Path(__file__).resolve().parent
_HTML_PATH = _HERE / "static" / "dashboard.html"


def build_dashboard_router() -> APIRouter:
    """Return a router that serves the operator dashboard at ``/dashboard``."""
    router = APIRouter(tags=["dashboard"])

    @router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard_index() -> HTMLResponse:
        return HTMLResponse(_HTML_PATH.read_text(encoding="utf-8"))

    @router.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/dashboard", status_code=307)

    return router
