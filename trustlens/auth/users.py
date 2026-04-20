"""User model and a dev-mode password store.

Passwords are salted + hashed with PBKDF2-HMAC-SHA256 (stdlib hashlib).
Production deployments should either:
    (a) use an SSO-only config (``LocalAuthProvider`` disabled, ``OIDCAuthProvider``
        carrying identity through a trusted IdP), or
    (b) replace this with a DB-backed store using bcrypt/argon2.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from trustlens.auth.rbac import Role


@dataclass
class User:
    user_id: str
    email: str
    display_name: str
    role: Role
    tenant_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    disabled: bool = False
    password_hash: Optional[str] = None   # None for SSO-only users


def hash_password(password: str, *, salt: Optional[bytes] = None,
                  iterations: int = 120_000) -> str:
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters_s, salt_hex, hash_hex = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  salt, iterations)
        return hmac.compare_digest(dk, expected)
    except (ValueError, AttributeError):
        return False


class UserStore(Protocol):
    def get(self, user_id: str) -> Optional[User]: ...
    def by_email(self, email: str) -> Optional[User]: ...
    def all(self) -> list[User]: ...
    def put(self, user: User) -> None: ...
    def delete(self, user_id: str) -> bool: ...


class InMemoryUserStore:
    def __init__(self, users: Optional[list[User]] = None) -> None:
        self._by_id: dict[str, User] = {}
        self._by_email: dict[str, str] = {}
        for u in users or []:
            self.put(u)

    def get(self, user_id: str) -> Optional[User]:
        return self._by_id.get(user_id)

    def by_email(self, email: str) -> Optional[User]:
        uid = self._by_email.get(email.lower())
        return self._by_id.get(uid) if uid else None

    def all(self) -> list[User]:
        return list(self._by_id.values())

    def put(self, user: User) -> None:
        self._by_id[user.user_id] = user
        self._by_email[user.email.lower()] = user.user_id

    def delete(self, user_id: str) -> bool:
        user = self._by_id.pop(user_id, None)
        if user is None:
            return False
        self._by_email.pop(user.email.lower(), None)
        return True
