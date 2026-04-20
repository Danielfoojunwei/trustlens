"""Auth routes: login, logout, session check, OIDC start/callback,
users CRUD, API key mint/list/revoke."""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from trustlens.auth.api_keys import ApiKeyStore
from trustlens.auth.dependencies import (
    COOKIE_NAME, current_user_or_none, require_permission,
)
from trustlens.auth.providers import AuthProvider
from trustlens.auth.rbac import Permission, Role, permissions_for
from trustlens.auth.sessions import SessionStore
from trustlens.auth.users import (
    InMemoryUserStore, User, UserStore, hash_password,
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class LoginBody(BaseModel):
    email: str
    password: str


class LoginResult(BaseModel):
    ok: bool
    user: Optional[dict] = None
    reason: Optional[str] = None


class CreateUserBody(BaseModel):
    email: str
    display_name: str
    role: str
    password: Optional[str] = None


class UpdateUserBody(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    disabled: Optional[bool] = None
    password: Optional[str] = None


class CreateKeyBody(BaseModel):
    tenant_id: str
    role: str
    name: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def build_auth_router(
    *,
    users: UserStore,
    sessions: SessionStore,
    api_keys: ApiKeyStore,
    provider: AuthProvider,
) -> APIRouter:
    router = APIRouter(prefix="/v1/auth", tags=["auth"])
    oidc_states: dict[str, float] = {}   # state → created_at

    # --- session / login ------------------------------------------------
    @router.post("/login", response_model=LoginResult)
    async def login(body: LoginBody, response: Response) -> LoginResult:
        res = await provider.authenticate_password(body.email, body.password)
        if not res.ok or res.user is None:
            return LoginResult(ok=False, reason=res.reason or "unknown")
        s = sessions.create(res.user.user_id)
        response.set_cookie(
            COOKIE_NAME, s.session_id,
            httponly=True, samesite="lax",
            path="/",
            max_age=int(s.expires_at - s.created_at),
        )
        return LoginResult(ok=True, user={
            "user_id": res.user.user_id,
            "email": res.user.email,
            "display_name": res.user.display_name,
            "role": res.user.role.value,
            "permissions": permissions_for(res.user.role),
        })

    @router.post("/logout")
    async def logout(
        response: Response,
        tl_session: Optional[str] = Cookie(default=None),
    ) -> dict:
        if tl_session:
            sessions.revoke(tl_session)
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @router.get("/me")
    async def whoami(
        user: Optional[User] = Depends(current_user_or_none),
    ) -> dict:
        if user is None:
            return {"authenticated": False}
        return {
            "authenticated": True,
            "user_id": user.user_id, "email": user.email,
            "display_name": user.display_name, "role": user.role.value,
            "permissions": permissions_for(user.role),
        }

    # --- OIDC flow ------------------------------------------------------
    @router.get("/oidc/start")
    async def oidc_start() -> dict:
        state = secrets.token_urlsafe(24)
        url = provider.start_redirect(state)
        if url is None:
            raise HTTPException(status_code=400, detail="provider_has_no_redirect")
        import time
        oidc_states[state] = time.time()
        return {"state": state, "url": url}

    @router.get("/oidc/callback")
    async def oidc_callback(
        code: str, state: str, response: Response,
    ) -> LoginResult:
        if state not in oidc_states:
            return LoginResult(ok=False, reason="bad_state")
        oidc_states.pop(state, None)
        res = await provider.exchange_code(code, state)
        if not res.ok or res.user is None:
            return LoginResult(ok=False, reason=res.reason or "oidc_failed")
        s = sessions.create(res.user.user_id)
        response.set_cookie(
            COOKIE_NAME, s.session_id,
            httponly=True, samesite="lax", path="/",
            max_age=int(s.expires_at - s.created_at),
        )
        return LoginResult(ok=True, user={
            "user_id": res.user.user_id, "email": res.user.email,
            "display_name": res.user.display_name, "role": res.user.role.value,
            "permissions": permissions_for(res.user.role),
        })

    # --- users ----------------------------------------------------------
    @router.get("/users")
    async def list_users(
        _: User = Depends(require_permission(Permission.USERS_READ)),
    ) -> list[dict]:
        return [{
            "user_id": u.user_id, "email": u.email,
            "display_name": u.display_name, "role": u.role.value,
            "tenant_ids": u.tenant_ids, "created_at": u.created_at,
            "disabled": u.disabled,
        } for u in users.all()]

    @router.post("/users")
    async def create_user(
        body: CreateUserBody,
        _: User = Depends(require_permission(Permission.USERS_WRITE)),
    ) -> dict:
        if users.by_email(body.email) is not None:
            raise HTTPException(status_code=409, detail="email_exists")
        try:
            role = Role(body.role)
        except ValueError:
            raise HTTPException(status_code=400, detail="bad_role")
        u = User(
            user_id=f"u_{secrets.token_hex(6)}",
            email=body.email.lower(),
            display_name=body.display_name,
            role=role,
            password_hash=hash_password(body.password) if body.password else None,
        )
        users.put(u)
        return {"user_id": u.user_id, "email": u.email, "role": u.role.value}

    @router.patch("/users/{user_id}")
    async def update_user(
        user_id: str, body: UpdateUserBody,
        _: User = Depends(require_permission(Permission.USERS_WRITE)),
    ) -> dict:
        u = users.get(user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="not_found")
        if body.display_name is not None: u.display_name = body.display_name
        if body.role is not None:
            try:
                u.role = Role(body.role)
            except ValueError:
                raise HTTPException(status_code=400, detail="bad_role")
        if body.disabled is not None: u.disabled = body.disabled
        if body.password: u.password_hash = hash_password(body.password)
        users.put(u)
        return {"ok": True}

    @router.delete("/users/{user_id}")
    async def delete_user(
        user_id: str,
        _: User = Depends(require_permission(Permission.USERS_WRITE)),
    ) -> dict:
        return {"ok": users.delete(user_id)}

    # --- API keys -------------------------------------------------------
    @router.get("/keys")
    async def list_keys(
        _: User = Depends(require_permission(Permission.APIKEYS_READ)),
    ) -> list[dict]:
        return [k.summary() for k in api_keys.all()]

    @router.post("/keys")
    async def create_key(
        body: CreateKeyBody,
        _: User = Depends(require_permission(Permission.APIKEYS_WRITE)),
    ) -> dict:
        try:
            role = Role(body.role)
        except ValueError:
            raise HTTPException(status_code=400, detail="bad_role")
        key, secret = api_keys.mint(body.tenant_id, role, body.name)
        return {"key": key.summary(), "secret": secret,
                "warning": "store this secret now — it will not be shown again"}

    @router.delete("/keys/{key_id}")
    async def revoke_key(
        key_id: str,
        _: User = Depends(require_permission(Permission.APIKEYS_WRITE)),
    ) -> dict:
        return {"ok": api_keys.revoke(key_id)}

    return router


def bootstrap_default_users(users: UserStore) -> None:
    """Seed a default owner if the store is empty — for demo only.

    Creates owner@trustlens.local / password ``trustlens``. Production must
    override this via the ``TRUSTLENS_BOOTSTRAP_*`` env vars or a proper
    user management flow.
    """
    if users.all():
        return
    import os
    email = os.environ.get("TRUSTLENS_BOOTSTRAP_EMAIL", "owner@trustlens.local")
    pw    = os.environ.get("TRUSTLENS_BOOTSTRAP_PASSWORD", "trustlens")
    users.put(User(
        user_id="u_owner", email=email, display_name="Default Owner",
        role=Role.OWNER, password_hash=hash_password(pw),
    ))
    users.put(User(
        user_id="u_viewer", email="viewer@trustlens.local",
        display_name="Viewer", role=Role.VIEWER,
        password_hash=hash_password("viewer"),
    ))
    users.put(User(
        user_id="u_operator", email="operator@trustlens.local",
        display_name="Operator", role=Role.OPERATOR,
        password_hash=hash_password("operator"),
    ))
