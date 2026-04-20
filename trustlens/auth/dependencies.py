"""FastAPI dependency helpers for auth + RBAC.

Resolution order for every protected request:

    1. Session cookie (``tl_session``)  — for browser users
    2. Authorization: Bearer <sk_...>   — for API-key clients

Returning a 401 if neither resolves. The ``require_permission`` factory
returns a dependency that additionally enforces an RBAC permission.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Cookie, Header, HTTPException

from trustlens.auth.api_keys import ApiKeyStore
from trustlens.auth.rbac import Permission, Role, role_has
from trustlens.auth.sessions import SessionStore
from trustlens.auth.users import User, UserStore


COOKIE_NAME = "tl_session"


class AuthContext:
    """A mounted context used by admin endpoints."""

    def __init__(
        self,
        users: UserStore,
        sessions: SessionStore,
        api_keys: ApiKeyStore,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.api_keys = api_keys


_CTX: Optional[AuthContext] = None


def set_auth_context(ctx: AuthContext) -> None:
    global _CTX
    _CTX = ctx


def get_auth_context() -> AuthContext:
    if _CTX is None:
        raise RuntimeError("auth context not initialized; call set_auth_context()")
    return _CTX


async def current_user_or_none(
    tl_session: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Optional[User]:
    ctx = _CTX
    if ctx is None:
        return None
    if tl_session:
        s = ctx.sessions.get(tl_session)
        if s is not None:
            return ctx.users.get(s.user_id)
    if authorization and authorization.lower().startswith("bearer "):
        secret = authorization.split(None, 1)[1].strip()
        if secret.startswith("sk_"):
            key = ctx.api_keys.verify(secret)
            if key is not None:
                # Materialize a synthetic user so downstream code can treat
                # API keys and sessions uniformly.
                return User(
                    user_id=f"apikey:{key.key_id}",
                    email=f"{key.key_id}@api",
                    display_name=f"apikey {key.name}",
                    role=key.role,
                )
    return None


def require_permission(perm: Permission):
    """Return a FastAPI dependency that enforces ``perm``."""

    async def _dep(
        tl_session: Optional[str] = Cookie(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> User:
        user = await current_user_or_none(
            tl_session=tl_session, authorization=authorization
        )
        if user is None:
            raise HTTPException(status_code=401, detail="not_authenticated")
        if not role_has(user.role, perm):
            raise HTTPException(status_code=403, detail=f"missing_permission:{perm.value}")
        return user

    return _dep
