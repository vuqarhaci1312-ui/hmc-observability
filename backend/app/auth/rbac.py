from dataclasses import dataclass, field
from uuid import UUID


class AuthorizationError(Exception):
    def __init__(self, message: str = "Forbidden") -> None:
        super().__init__(message)
        self.message = message


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "super_admin": {"*"},
    "noc_operator": {
        "telemetry:read",
        "inventory:read",
        "alerts:read",
        "alerts:write",
        "incidents:read",
        "incidents:write",
        "scenarios:read",
        "scenarios:write",
        "simulation:read",
        "simulation:write",
        "audit:read",
    },
    "tenant_admin": {
        "telemetry:read",
        "inventory:read",
        "alerts:read",
        "alerts:write",
        "incidents:read",
        "incidents:write",
        "scenarios:read",
        "scenarios:write",
        "settings:write",
        "audit:read",
    },
    "tenant_viewer": {
        "telemetry:read",
        "inventory:read",
        "alerts:read",
        "incidents:read",
        "scenarios:read",
    },
}


@dataclass
class Principal:
    user_id: UUID
    email: str
    role: str
    tenant_ids: set[UUID] = field(default_factory=set)
    token_kind: str = "session"

    def allows(self, permission: str) -> bool:
        granted = ROLE_PERMISSIONS.get(self.role, set())
        return "*" in granted or permission in granted


def require_permission(principal: Principal, permission: str) -> None:
    if not principal.allows(permission):
        raise AuthorizationError("Missing permission")


def tenant_filter(principal: Principal, requested: UUID | None) -> set[UUID] | None:
    """Return the tenant ids the query may use.

    None means every tenant, which is only available to a super admin.
    """
    if principal.role == "super_admin":
        if requested is None:
            return None
        return {requested}
    if requested is not None and requested not in principal.tenant_ids:
        raise AuthorizationError("Tenant access denied")
    if requested is not None:
        return {requested}
    if not principal.tenant_ids:
        raise AuthorizationError("Tenant access denied")
    return set(principal.tenant_ids)


def source_types_for_mode(mode: str) -> list[str] | None:
    if mode == "demo":
        return ["demo"]
    if mode == "real":
        return ["real"]
    if mode == "mixed":
        return None
    raise ValueError("Unknown data mode")
