"""Session store — cookie-backed, in-memory today.

Swap for Redis in multi-replica deployments. Session cookies are
``HttpOnly``, ``Secure``, ``SameSite=Lax`` by the issuing endpoint.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class Session:
    session_id: str
    user_id: str
    created_at: float
    expires_at: float

    def is_valid(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        return now < self.expires_at


class SessionStore(Protocol):
    def create(self, user_id: str, ttl_s: int = 28800) -> Session: ...
    def get(self, session_id: str) -> Optional[Session]: ...
    def revoke(self, session_id: str) -> bool: ...
    def for_user(self, user_id: str) -> list[Session]: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Session] = {}

    def create(self, user_id: str, ttl_s: int = 28800) -> Session:
        sid = secrets.token_urlsafe(32)
        now = time.time()
        s = Session(session_id=sid, user_id=user_id,
                    created_at=now, expires_at=now + ttl_s)
        self._by_id[sid] = s
        return s

    def get(self, session_id: str) -> Optional[Session]:
        s = self._by_id.get(session_id)
        if s is None:
            return None
        if not s.is_valid():
            self._by_id.pop(session_id, None)
            return None
        return s

    def revoke(self, session_id: str) -> bool:
        return self._by_id.pop(session_id, None) is not None

    def for_user(self, user_id: str) -> list[Session]:
        return [s for s in self._by_id.values() if s.user_id == user_id and s.is_valid()]
