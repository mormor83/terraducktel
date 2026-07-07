"""Role-Based Access Control: role hierarchy and FastAPI dependencies."""
from enum import Enum

from fastapi import Depends, HTTPException, Request, status

from app.auth.bu_context import bu_role_cap
from app.auth.jwt import get_current_user
from app.models.user import User
from app.services import api_key_service


class Role(str, Enum):
    admin = "admin"
    operator = "operator"
    viewer = "viewer"


ROLE_HIERARCHY: dict[Role, int] = {
    Role.viewer: 0,
    Role.operator: 1,
    Role.admin: 2,
}

# An API key's capability tier maps onto an *effective* role ceiling, floored
# by the owner's own role (a key never exceeds its owner). `read` caps at
# viewer, `plan`/`apply` at operator, and `admin` at admin — an admin key acts
# with full admin authority *within its BU*. The per-endpoint
# api_key_service.enforce() then draws the finer plan-vs-apply line and applies
# the workspace allowlist; identity endpoints (key/user/BU management) are held
# interactive-only by api_key_service.forbid_api_keys regardless of this map.
_CAPABILITY_ROLE: dict[str, Role] = {
    "read": Role.viewer,
    "plan": Role.operator,
    "apply": Role.operator,
    "admin": Role.admin,
}


def require_role(minimum_role: Role):
    """FastAPI dependency factory: reject users below the minimum role level.

    For API-key callers the effective role is the *minimum* of the owner's role
    and the key's capability ceiling (`_CAPABILITY_ROLE` above) — a key can
    never exceed its owner's real role, but an `admin`-tier key owned by an
    admin user *does* satisfy `require_role(Role.admin)`. Endpoints that must
    stay interactive-only regardless of capability tier (identity/key/BU
    management) additionally guard with `api_key_service.forbid_api_keys`.
    """
    async def checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        membership_role: "str | None" = Depends(bu_role_cap),
    ) -> User:
        user_role = Role(current_user.role)
        key = api_key_service.get_request_key(request)
        if key is not None:
            cap_role = _CAPABILITY_ROLE.get(key.capability, Role.viewer)
            if ROLE_HIERARCHY[cap_role] < ROLE_HIERARCHY[user_role]:
                user_role = cap_role
        # Run-scoped executor token: can never satisfy require_role
        # above operator, so a superadmin-triggered run can't reach admin-gated
        # endpoints even on its allowlisted callback routes.
        if getattr(request.state, "run_token", None) is not None:
            if ROLE_HIERARCHY[Role.operator] < ROLE_HIERARCHY[user_role]:
                user_role = Role.operator
        # Per-BU role enforcement ("Model 2"): floor a non-admin's
        # effective role by their membership role in the active BU. A global
        # `admin` and superadmins are exempt (there is no per-BU admin role);
        # `membership_role` is None (no cap) for them and for API-key/run-token
        # scopes and endpoints without a resolvable BU.
        elif (
            user_role != Role.admin
            and not bool(getattr(current_user, "is_superadmin", False))
            and membership_role is not None
        ):
            try:
                m = Role(membership_role)
            except ValueError:
                m = Role.viewer
            if ROLE_HIERARCHY[m] < ROLE_HIERARCHY[user_role]:
                user_role = m
        if ROLE_HIERARCHY[user_role] < ROLE_HIERARCHY[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum_role.value} role or higher",
            )
        return current_user
    return checker
