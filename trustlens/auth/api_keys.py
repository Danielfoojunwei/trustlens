"""API keys — for programmatic callers.

Each key carries:
    - id (public, opaque; looks like ``tlk_XXXXXX``)
    - tenant_id (may be ``*`` for multi-tenant keys on admin scope)
    - role (what permissions the key can exercise)
    - a SHA-256 hash of the secret (the plaintext is shown only at creation)

Rotating a key is just: create a new one → distribute → delete the old.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from trustlens.auth.rbac import Role


def _gen_key() -> tuple[str, str]:
    """Return (public_id, plaintext_secret). Store only hash(secret)."""
    pub = "tlk_" + secrets.token_urlsafe(12)
    sec = "sk_"  + secrets.token_urlsafe(32)
    return pub, sec


def hash_api_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass
class ApiKey:
    key_id: str                       # public id, e.g. tlk_ABC123
    tenant_id: str                    # "*" for fleet-wide admin keys
    role: Role
    name: str
    hash: str                         # SHA-256 of secret
    created_at: float = field(default_factory=time.time)
    last_used_at: Optional[float] = None
    disabled: bool = False

    def summary(self) -> dict:
        return {
            "key_id": self.key_id, "tenant_id": self.tenant_id,
            "role": self.role.value, "name": self.name,
            "created_at": self.created_at, "last_used_at": self.last_used_at,
            "disabled": self.disabled,
        }


class ApiKeyStore(Protocol):
    def mint(self, tenant_id: str, role: Role, name: str) -> tuple[ApiKey, str]: ...
    def get(self, key_id: str) -> Optional[ApiKey]: ...
    def verify(self, secret: str) -> Optional[ApiKey]: ...
    def revoke(self, key_id: str) -> bool: ...
    def all(self) -> list[ApiKey]: ...


class InMemoryApiKeyStore:
    def __init__(self) -> None:
        self._by_id: dict[str, ApiKey] = {}
        self._hash_to_id: dict[str, str] = {}

    def mint(self, tenant_id: str, role: Role, name: str) -> tuple[ApiKey, str]:
        kid, secret = _gen_key()
        h = hash_api_key(secret)
        key = ApiKey(key_id=kid, tenant_id=tenant_id, role=role, name=name, hash=h)
        self._by_id[kid] = key
        self._hash_to_id[h] = kid
        return key, secret

    def get(self, key_id: str) -> Optional[ApiKey]:
        return self._by_id.get(key_id)

    def verify(self, secret: str) -> Optional[ApiKey]:
        kid = self._hash_to_id.get(hash_api_key(secret))
        if kid is None:
            return None
        k = self._by_id.get(kid)
        if k is None or k.disabled:
            return None
        k.last_used_at = time.time()
        return k

    def revoke(self, key_id: str) -> bool:
        k = self._by_id.pop(key_id, None)
        if k is None:
            return False
        self._hash_to_id.pop(k.hash, None)
        return True

    def all(self) -> list[ApiKey]:
        return list(self._by_id.values())
