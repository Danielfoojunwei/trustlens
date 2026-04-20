"""Auth + RBAC + API keys for the TrustLens gateway.

Exposes:
    - Role, Permission, role_has
    - User, Session, SessionStore
    - AuthProvider (protocol), LocalAuthProvider, OIDCAuthProvider
    - ApiKey, ApiKeyStore
    - require_permission (FastAPI dependency factory)

The design is pluggable: swap LocalAuthProvider for OIDCAuthProvider to wire
an SSO upstream (Google / Okta / Azure AD / Auth0). Session state is
in-process today; for multi-replica deployments, swap SessionStore for a
Redis-backed implementation.
"""

from trustlens.auth.rbac import Permission, Role, role_has
from trustlens.auth.users import User, UserStore, InMemoryUserStore, hash_password, verify_password
from trustlens.auth.sessions import Session, SessionStore, InMemorySessionStore
from trustlens.auth.api_keys import ApiKey, ApiKeyStore, InMemoryApiKeyStore, hash_api_key
from trustlens.auth.providers import AuthProvider, LocalAuthProvider, OIDCAuthProvider
from trustlens.auth.dependencies import require_permission, current_user_or_none

__all__ = [
    "Permission", "Role", "role_has",
    "User", "UserStore", "InMemoryUserStore",
    "Session", "SessionStore", "InMemorySessionStore",
    "ApiKey", "ApiKeyStore", "InMemoryApiKeyStore",
    "AuthProvider", "LocalAuthProvider", "OIDCAuthProvider",
    "require_permission", "current_user_or_none",
    "hash_password", "verify_password", "hash_api_key",
]
