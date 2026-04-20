"""Role-based access control.

Four built-in roles. Each role carries a fixed permission set.

    OWNER     — full control including user management and integrations
    ADMIN     — read+write everything except user management
    OPERATOR  — can use the playground, manage KB, view incidents
    VIEWER    — read-only: overview, analytics, certs, incidents

Permissions are strings (stable IDs) so they survive config dumps & audit
logs. The table below is the source of truth.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Permission(str, Enum):
    # Monitoring
    VIEW_OVERVIEW      = "view.overview"
    VIEW_ANALYTICS     = "view.analytics"
    VIEW_CERTS         = "view.certs"
    VIEW_INCIDENTS     = "view.incidents"
    VIEW_AXES          = "view.axes"

    # KB
    KB_READ            = "kb.read"
    KB_WRITE           = "kb.write"
    KB_DELETE          = "kb.delete"
    KB_EXPORT          = "kb.export"

    # Verifier / settings
    VERIFIER_TUNE      = "verifier.tune"
    VERIFIER_SETTINGS  = "verifier.settings"
    INTEGRATIONS_READ  = "integrations.read"
    INTEGRATIONS_WRITE = "integrations.write"

    # Incident management
    INCIDENTS_ACK      = "incidents.ack"
    INCIDENTS_WEBHOOK  = "incidents.webhook"

    # Users & access
    USERS_READ         = "users.read"
    USERS_WRITE        = "users.write"
    APIKEYS_READ       = "apikeys.read"
    APIKEYS_WRITE      = "apikeys.write"


_PERMS: dict[Role, set[Permission]] = {
    Role.OWNER: set(Permission),  # all permissions
    Role.ADMIN: {
        Permission.VIEW_OVERVIEW, Permission.VIEW_ANALYTICS, Permission.VIEW_CERTS,
        Permission.VIEW_INCIDENTS, Permission.VIEW_AXES,
        Permission.KB_READ, Permission.KB_WRITE, Permission.KB_DELETE, Permission.KB_EXPORT,
        Permission.VERIFIER_TUNE, Permission.VERIFIER_SETTINGS,
        Permission.INTEGRATIONS_READ, Permission.INTEGRATIONS_WRITE,
        Permission.INCIDENTS_ACK, Permission.INCIDENTS_WEBHOOK,
        Permission.APIKEYS_READ, Permission.APIKEYS_WRITE,
    },
    Role.OPERATOR: {
        Permission.VIEW_OVERVIEW, Permission.VIEW_ANALYTICS, Permission.VIEW_CERTS,
        Permission.VIEW_INCIDENTS, Permission.VIEW_AXES,
        Permission.KB_READ, Permission.KB_WRITE, Permission.KB_EXPORT,
        Permission.VERIFIER_TUNE,
        Permission.INTEGRATIONS_READ,
        Permission.INCIDENTS_ACK,
    },
    Role.VIEWER: {
        Permission.VIEW_OVERVIEW, Permission.VIEW_ANALYTICS, Permission.VIEW_CERTS,
        Permission.VIEW_INCIDENTS, Permission.VIEW_AXES,
        Permission.KB_READ,
        Permission.INTEGRATIONS_READ,
    },
}


def role_has(role: Role, permission: Permission) -> bool:
    return permission in _PERMS.get(role, set())


def permissions_for(role: Role) -> list[str]:
    return sorted(p.value for p in _PERMS.get(role, set()))
