"""Pluggable auth providers.

LocalAuthProvider
    Password login against a ``UserStore``. Good for dev + small teams.

OIDCAuthProvider
    Redirect-based OAuth2 / OpenID Connect. Ships the redirect URL + the
    callback handler; uses the installed ``httpx`` to exchange the code.
    Requires the IdP's ``discovery URL`` (e.g. Google:
    ``https://accounts.google.com/.well-known/openid-configuration``).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from trustlens.auth.rbac import Role
from trustlens.auth.users import User, UserStore, verify_password


@dataclass
class AuthResult:
    ok: bool
    user: Optional[User] = None
    reason: Optional[str] = None


class AuthProvider(Protocol):
    name: str
    async def authenticate_password(
        self, email: str, password: str
    ) -> AuthResult: ...
    def start_redirect(self, state: str) -> Optional[str]: ...
    async def exchange_code(self, code: str, state: str) -> AuthResult: ...


@dataclass
class LocalAuthProvider:
    users: UserStore
    name: str = "local"

    async def authenticate_password(self, email: str, password: str) -> AuthResult:
        u = self.users.by_email(email)
        if u is None:
            return AuthResult(ok=False, reason="unknown_user")
        if u.disabled:
            return AuthResult(ok=False, reason="disabled")
        if not u.password_hash or not verify_password(password, u.password_hash):
            return AuthResult(ok=False, reason="bad_password")
        return AuthResult(ok=True, user=u)

    def start_redirect(self, state: str) -> Optional[str]:
        return None  # no redirect flow for local auth

    async def exchange_code(self, code: str, state: str) -> AuthResult:
        return AuthResult(ok=False, reason="no_redirect_flow")


@dataclass
class OIDCAuthProvider:
    """Minimal OIDC redirect adapter. Depends on ``httpx`` (already a core dep).

    Args:
        client_id / client_secret: from your IdP console
        discovery_url: e.g. https://accounts.google.com/.well-known/openid-configuration
        redirect_uri: must match IdP console
        users: where to look up the user after callback
        default_role: role assigned if a new user signs in and we auto-provision
        auto_provision: if True, unknown-email callbacks are added to the user
                        store with `default_role`
    """
    client_id: str
    client_secret: str
    discovery_url: str
    redirect_uri: str
    users: UserStore
    default_role: Role = Role.VIEWER
    auto_provision: bool = False
    name: str = "oidc"
    _config: dict = field(default_factory=dict)

    async def _ensure_config(self) -> None:
        if self._config:
            return
        import httpx  # type: ignore
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(self.discovery_url)
            r.raise_for_status()
            self._config = r.json()

    async def authenticate_password(self, email: str, password: str) -> AuthResult:
        return AuthResult(ok=False, reason="oidc_password_unsupported")

    def start_redirect(self, state: str) -> Optional[str]:
        """Return the IdP login URL. ``_ensure_config`` is lazy so we can
        synchronously return a URL; the discovery data is cached per-process
        via background call. If missing, we fall back to Google defaults."""
        auth_ep = (self._config.get("authorization_endpoint") or
                   "https://accounts.google.com/o/oauth2/v2/auth")
        params = [
            ("client_id", self.client_id),
            ("response_type", "code"),
            ("redirect_uri", self.redirect_uri),
            ("scope", "openid email profile"),
            ("state", state),
        ]
        from urllib.parse import urlencode
        return f"{auth_ep}?{urlencode(params)}"

    async def exchange_code(self, code: str, state: str) -> AuthResult:
        await self._ensure_config()
        token_ep = self._config.get("token_endpoint")
        userinfo_ep = self._config.get("userinfo_endpoint")
        if not token_ep or not userinfo_ep:
            return AuthResult(ok=False, reason="oidc_discovery_missing")
        import httpx  # type: ignore
        async with httpx.AsyncClient(timeout=10.0) as c:
            tok = await c.post(token_ep, data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
            })
            if tok.status_code >= 400:
                return AuthResult(ok=False, reason="oidc_token_failed")
            tok_j = tok.json()
            access_token = tok_j.get("access_token")
            if not access_token:
                return AuthResult(ok=False, reason="oidc_no_access_token")
            ui = await c.get(userinfo_ep,
                             headers={"Authorization": f"Bearer {access_token}"})
            if ui.status_code >= 400:
                return AuthResult(ok=False, reason="oidc_userinfo_failed")
            claims = ui.json()
        email = claims.get("email", "").lower()
        if not email:
            return AuthResult(ok=False, reason="oidc_no_email")
        u = self.users.by_email(email)
        if u is None:
            if not self.auto_provision:
                return AuthResult(ok=False, reason="unknown_user")
            u = User(
                user_id=f"oidc-{secrets.token_hex(6)}",
                email=email,
                display_name=claims.get("name") or email,
                role=self.default_role,
                password_hash=None,
            )
            self.users.put(u)
        if u.disabled:
            return AuthResult(ok=False, reason="disabled")
        return AuthResult(ok=True, user=u)
