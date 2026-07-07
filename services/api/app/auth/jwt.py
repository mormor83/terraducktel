"""JWT authentication: issue and verify tokens, password hashing.

`get_current_user` also accepts long-lived **API keys** (bearer tokens prefixed
`tdt_`, see app/services/api_key_service.py). An API key authenticates AS its
owning user; the key record is stashed on `request.state.api_key` so downstream
dependencies (`current_bu`) and handlers (`api_key_service.enforce`) can apply
the key's narrower BU + capability + workspace scope. The JWT path is unchanged.
"""
import os
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.api_key import APIKey
from app.models.user import User
from app.services import api_key_service

# JWT settings — defaults used when no override is passed in. The active
# values for *user-issued* tokens are admin-tunable via the `config` table
# under `auth.access_token_expire_minutes` / `auth.refresh_token_expire_hours`
# (see app/services/runtime_settings.py). Internal machine tokens (executor
# callbacks, approval links) still use these defaults so that operator-tuning
# the session length never accidentally shortens a long-running apply.
_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours
_REFRESH_TOKEN_EXPIRE_HOURS = 24

# Secret: prefer config table via env override; fall back to env var for bootstrap
_JWT_SECRET: str | None = os.environ.get("JWT_SECRET_KEY")

_bearer_scheme = HTTPBearer(auto_error=False)


def _get_secret() -> str:
    """Return JWT secret. Raises RuntimeError if not configured."""
    secret = _JWT_SECRET or os.environ.get("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY must be configured")
    return secret


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against its bcrypt hash.

    Fails closed (returns False) when the stored value isn't a valid bcrypt
    hash — e.g. the `!OIDC!` sentinel on OIDC-provisioned users. Without this,
    `bcrypt.checkpw` raises ValueError on a local-login attempt for such a
    user, surfacing as a 500 that doubles as an account-enumeration oracle.
    """
    try:
        return _bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
    except (ValueError, TypeError):
        return False


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    is_superadmin: bool = False,
    name: str | None = None,
    expires_in_minutes: int | float | None = None,
) -> str:
    """Create a short-lived access JWT.

    `is_superadmin` is surfaced as a top-level claim so the UI can render
    the Business Unit switcher's "All BUs" affordance without a separate
    /me call. `name` is the user's display name (from OIDC `name`/given+family/
    preferred_username, populated by upsert_oidc_user); NULL for local users.

    `expires_in_minutes` lets the caller override the lifetime — the auth
    router passes the admin-configured value from `runtime_settings`; internal
    callers (executor callbacks, approval links) omit it and get the module
    default.
    """
    minutes = (
        float(expires_in_minutes)
        if expires_in_minutes is not None
        else _ACCESS_TOKEN_EXPIRE_MINUTES
    )
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    payload: dict = {
        "sub": user_id,
        "email": email,
        "role": role,
        "is_superadmin": bool(is_superadmin),
        "type": "access",
        "exp": expire,
    }
    if name:
        payload["name"] = name
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def create_refresh_token(
    user_id: str,
    expires_in_hours: int | float | None = None,
) -> str:
    """Create a longer-lived refresh JWT.

    `expires_in_hours` lets the auth router pass the admin-configured value.
    """
    hours = (
        float(expires_in_hours)
        if expires_in_hours is not None
        else _REFRESH_TOKEN_EXPIRE_HOURS
    )
    expire = datetime.now(timezone.utc) + timedelta(hours=hours)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


_RUN_TOKEN_EXPIRE_MINUTES = 24 * 60  # must outlive the longest single executor phase


def create_run_token(
    user_id: str,
    email: str,
    *,
    run_id: str,
    workspace_id: str,
    business_unit_id: str | None,
    expires_in_minutes: int | float | None = None,
) -> str:
    """Mint a run-scoped service token for the executor.

    Deliberately carries NO `role` / `is_superadmin` claim: `get_current_user`
    confines this token to the handful of run-callback routes for exactly this
    `run_id`, `current_bu` pins it to the run's BU (never all-BU), and
    `require_role` caps it at operator. `sub` stays the triggering user so
    `triggered_by` / audit semantics are unchanged, but the token can never act
    as that user anywhere else — even if the user is a superadmin.
    """
    minutes = (
        float(expires_in_minutes)
        if expires_in_minutes is not None
        else _RUN_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user_id,
        "email": email,
        "type": "run",
        "run_id": run_id,
        "workspace_id": workspace_id,
        "business_unit_id": business_unit_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])


# The ONLY routes a run-scoped executor token may authenticate on — keyed by
# endpoint function name (FastAPI route.name). Keep in sync with the executor's
# callbacks in services/executor/entrypoint.sh; the test_run_token suite guards
# this list. Renaming any of these handlers WILL break executors.
_RUN_TOKEN_ROUTES = {
    "patch_run",
    "list_run_steps",
    "patch_run_step",
    "get_run_policies",
    "heartbeat_run",
    "get_run_tfplan",
}


async def _resolve_api_key(token: str, request: Request, db: AsyncSession) -> User:
    """Authenticate an API-key bearer token.

    On success, stashes the key on `request.state.api_key`, bumps `last_used_at`,
    and returns the owning user. Raises 401 if the key is unknown, revoked, or
    expired.
    """
    key = (
        await db.execute(
            select(APIKey).where(APIKey.token_hash == api_key_service.hash_token(token))
        )
    ).scalars().first()
    if key is None or not api_key_service.is_active(key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key",
        )
    user = await db.get(User, key.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key owner not found",
        )
    request.state.api_key = key
    # Best-effort last-used stamp; never block auth on the write.
    key.last_used_at = datetime.now(timezone.utc)
    try:
        await db.commit()
    except Exception:  # noqa: BLE001 — stamping is non-critical
        await db.rollback()
    return user


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency: resolve the caller from a JWT or an API key.

    Bearer tokens prefixed `tdt_` are treated as API keys; everything else is
    decoded as a JWT (unchanged behavior).
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    if api_key_service.looks_like_api_key(credentials.credentials):
        return await _resolve_api_key(credentials.credentials, request, db)
    try:
        payload = decode_token(credentials.credentials)
        tok_type = payload.get("type")
        if tok_type == "run":
            # Run-scoped executor token: valid ONLY on the executor's
            # own callback routes, and ONLY for its own run_id. Everything else
            # (approve, cancel, trigger, workspaces, users, …) is unreachable
            # regardless of the subject's DB role.
            route = request.scope.get("route")
            if (
                getattr(route, "name", None) not in _RUN_TOKEN_ROUTES
                or str(request.path_params.get("run_id")) != str(payload.get("run_id"))
                or not payload.get("workspace_id")
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Run token not valid for this endpoint",
                )
            request.state.run_token = payload
        elif tok_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user
