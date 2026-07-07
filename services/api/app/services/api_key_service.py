"""API key service: generate, resolve, and enforce scoped API keys.

A key's plaintext (`tdt_<urlsafe>`) is shown to the admin once at creation; we
store only its SHA-256 hash plus a short display prefix. Lookup is an indexed
exact match on the hash — no plaintext is ever persisted or returned again.

Capability tiers are ordered read < plan < apply < admin:
  read  — viewer-equivalent; may read runs/plans/workspaces in scope.
  plan  — may also trigger plan-only runs and cancel them.
  apply — may also trigger apply/destroy runs and approve/reject.
  admin — acts with the owner's full admin role *within the key's BU*:
          create / discover / import / update / delete workspaces, manage AWS
          accounts, clusters, policies, drift, integrations, variables, etc.
          The one carve-out is *identity*: an admin key can never mint/revoke
          API keys, manage users, or create/update Business Units — those stay
          interactive-only (see `forbid_api_keys`). A key is always bound to
          exactly one BU and `bu_context` ignores `X-Business-Unit` for keys,
          so even an admin key cannot reach across BUs.

`enforce()` is the single guard called from run/approval/workspace handlers. It
is a no-op for interactive (JWT) callers — their require_role already governs —
and raises 403 for API-key callers that exceed their tier or step outside their
workspace allowlist. `forbid_api_keys()` is the blanket "no automation here"
gate for identity routers.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status

from app.models.api_key import APIKey

TOKEN_PREFIX = "tdt_"

# Ascending capability ranks. Higher = more power. `admin` is the full-control
# tier (everything but identity — see module docstring + forbid_api_keys).
CAPABILITY_RANK: dict[str, int] = {"read": 0, "plan": 1, "apply": 2, "admin": 3}
CAPABILITIES = tuple(CAPABILITY_RANK.keys())


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a plaintext token. Used for storage and lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """Mint a new token.

    Returns (plaintext, token_prefix, token_hash). The plaintext is returned to
    the caller exactly once; only the prefix + hash are meant to be persisted.
    """
    secret = secrets.token_urlsafe(32)
    plaintext = f"{TOKEN_PREFIX}{secret}"
    # Display fragment: scheme + first 6 chars of the secret, e.g. "tdt_ab12cd".
    prefix = f"{TOKEN_PREFIX}{secret[:6]}"
    return plaintext, prefix, hash_token(plaintext)


def looks_like_api_key(token: str | None) -> bool:
    """Cheap check: does this bearer token look like an API key (vs a JWT)?"""
    return bool(token) and token.startswith(TOKEN_PREFIX)


def is_active(key: APIKey, *, now: datetime | None = None) -> bool:
    """True if the key is neither revoked nor expired."""
    now = now or datetime.now(timezone.utc)
    if key.revoked_at is not None:
        return False
    if key.expires_at is not None:
        exp = key.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            return False
    return True


def can(key: APIKey, *, need: str, workspace_id: str | None) -> bool:
    """Does `key` satisfy capability `need` for `workspace_id`?

    Tier must be >= the required tier, and — when the key has a non-empty
    workspace allowlist — the target workspace must be in it. A None
    workspace_id (BU-wide action) is allowed only when no allowlist is set.
    """
    if CAPABILITY_RANK.get(key.capability, 0) < CAPABILITY_RANK[need]:
        return False
    allow = key.workspace_ids or []
    if allow:
        if workspace_id is None or workspace_id not in allow:
            return False
    return True


def get_request_key(request: Request | None) -> APIKey | None:
    """Return the APIKey attached to this request, if it was API-key-authed."""
    if request is None:
        return None
    return getattr(request.state, "api_key", None)


def enforce(request: Request | None, *, need: str, workspace_id: str | None) -> None:
    """Guard a write/scoped action for API-key callers.

    No-op for interactive (JWT) callers. For API-key callers, raises 403 unless
    the key's capability tier and workspace allowlist permit the action.
    """
    key = get_request_key(request)
    if key is None:
        return
    if not can(key, need=need, workspace_id=workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"API key capability '{key.capability}' does not permit this "
                f"action (requires '{need}')"
                if CAPABILITY_RANK.get(key.capability, 0) < CAPABILITY_RANK[need]
                else "API key is not scoped to this workspace"
            ),
        )


def block_api_keys(request: Request | None, *, action: str = "perform this action") -> None:
    """Reject API-key callers outright, regardless of tier; no-op for JWT callers.

    For *identity* endpoints automation must never reach even at the `admin`
    tier — minting/revoking keys, user management, Business-Unit CRUD. Unlike
    `enforce`, this ignores the key's capability entirely: an `admin` key is
    still rejected here. (Critical: some identity handlers gate on the owning
    user's `is_superadmin`, which an admin key would otherwise inherit.)
    """
    if get_request_key(request) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API keys cannot {action}; use an interactive (logged-in) session",
        )


def forbid_api_keys(action: str = "perform this action"):
    """FastAPI dependency factory that blocks API-key callers for a whole router.

    Attach to an identity router via `APIRouter(..., dependencies=[Depends(
    api_key_service.forbid_api_keys("manage API keys"))])` so every route in it
    is interactive-only, independent of capability tier.

    The inner dep takes a dependency on `get_current_user` so it is resolved
    *after* the key has been stashed on `request.state` — a router-level
    dependency has no implicit ordering edge to the auth dependency otherwise.
    """
    # Lazy import: app.auth.jwt imports this module, so importing it at module
    # load time would be circular.
    from app.auth.jwt import get_current_user

    def _dep(request: Request, _user=Depends(get_current_user)) -> None:
        block_api_keys(request, action=action)

    return _dep


def allowlist(request: Request | None) -> list[str] | None:
    """The key's workspace allowlist for filtering reads, or None (no filter)."""
    key = get_request_key(request)
    if key is None:
        return None
    return key.workspace_ids or None
