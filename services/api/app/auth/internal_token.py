"""Service tokens for the Terraform HTTP backend and the internal API.

These are two DELIBERATELY SEPARATE secrets with different trust levels:

  - `TERRADUCKTEL_STATE_TOKEN` guards only the Terraform HTTP state backend
    (`app/routers/state.py`: get/put state, lock/unlock). It is handed to
    every ephemeral executor container so `terraform init/plan/apply` can
    reach its own workspace's state over Terraform's `backend "http"`, which
    does NOT support arbitrary custom headers — only `TF_HTTP_USERNAME`/
    `TF_HTTP_PASSWORD` (HTTP Basic Auth). We accept both that and the
    `X-Terraducktel-State-Token` header (used by drift-detector, manual curl,
    tests).

  - `TERRADUCKTEL_INTERNAL_TOKEN` guards the cross-tenant `/api/v1/internal/*`
    router (list every workspace, hand out plaintext AWS credentials and the
    platform GitHub token, delete a workspace) — used ONLY by the
    drift-detector and liveness-detector background services, which run on
    the private Docker network and never execute untrusted Terraform/Helm
    code. It is intentionally NEVER injected into executor containers: an
    executor runs a workspace's own (semi-trusted, possibly third-party)
    Terraform code, and handing it the internal token would let a single
    `local-exec` provisioner or malicious module read its own container's
    env and pivot to every other tenant's AWS credentials. See
    docs/claude/executor.md for the rationale — do not merge the two
    tokens back together.

Fail-loud: if either env var is missing on first call, raise RuntimeError so
the operator gets a hard signal instead of silently leaving endpoints
unprotected.
"""
import base64
import binascii
import hmac
import os

from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, status

_HEADER_NAME = "X-Terraducktel-State-Token"
_INTERNAL_HEADER_NAME = "X-Terraducktel-Internal-Token"


@dataclass
class StateAuth:
    """Result of state-backend authentication.

    `workspace_id is None` → global scope (drift-detector / ops via the global
    token): may touch any workspace's state. A concrete `workspace_id` → the
    caller is a run-scoped executor token and may ONLY touch that workspace's
    state.
    """
    workspace_id: Optional[str]


def _expected_token() -> str:
    raw = os.environ.get("TERRADUCKTEL_STATE_TOKEN")
    if not raw:
        raise RuntimeError("TERRADUCKTEL_STATE_TOKEN must be configured")
    return raw


def _expected_internal_token() -> str:
    raw = os.environ.get("TERRADUCKTEL_INTERNAL_TOKEN")
    if not raw:
        raise RuntimeError("TERRADUCKTEL_INTERNAL_TOKEN must be configured")
    return raw


def _basic_auth_password(authorization: str | None) -> str | None:
    """Extract the password from an HTTP Basic auth header, or None."""
    if not authorization or not authorization.lower().startswith("basic "):
        return None
    try:
        encoded = authorization.split(" ", 1)[1].strip()
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8", errors="replace")
    except (binascii.Error, UnicodeDecodeError, IndexError):
        return None
    if ":" not in decoded:
        return None
    return decoded.split(":", 1)[1]


async def require_state_token(
    x_terraducktel_state_token: str | None = Header(default=None, alias=_HEADER_NAME),
    authorization: str | None = Header(default=None),
) -> StateAuth:
    """FastAPI dependency for the Terraform HTTP state backend.

    Returns a `StateAuth` describing the caller's scope; the state router then
    enforces that a run-scoped executor token only touches its own workspace. Auth paths:

      B1. HTTP Basic password is a run-scoped JWT (`type="run"`) → scoped to
          that token's `workspace_id`. This is what executors now send
          (TF_HTTP_PASSWORD = the run token). Works even if the global state
          token is unconfigured (it's a JWT, verified with the JWT secret).
      A.  `X-Terraducktel-State-Token` header equals the global token → global
          scope (drift-detector / ops / manual curl).
      B2. HTTP Basic password equals the global token → global scope
          (deprecated back-compat; executors no longer hold the global token).

    401 on no match; 503 if the global token is needed but unconfigured.
    """
    basic_pw = _basic_auth_password(authorization)

    # B1: run-scoped executor token via Basic auth — the preferred path.
    if basic_pw:
        try:
            from app.auth.jwt import decode_token

            payload = decode_token(basic_pw)
            if payload.get("type") == "run" and payload.get("workspace_id"):
                return StateAuth(workspace_id=str(payload["workspace_id"]))
        except Exception:  # noqa: BLE001 — not a run JWT; fall through to global paths
            pass

    # Global-scope paths need the configured global token.
    try:
        expected = _expected_token()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="State token not configured",
        )
    # A: custom header (drift-detector / curl / existing tests).
    if x_terraducktel_state_token is not None and hmac.compare_digest(
        x_terraducktel_state_token, expected
    ):
        return StateAuth(workspace_id=None)
    # B2: global token via Basic (deprecated — executors no longer send it).
    if basic_pw is not None and hmac.compare_digest(basic_pw, expected):
        return StateAuth(workspace_id=None)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing state token",
    )


async def require_internal_token(
    x_terraducktel_internal_token: str | None = Header(
        default=None, alias=_INTERNAL_HEADER_NAME
    ),
) -> None:
    """FastAPI dependency: 401 unless the internal-only token header matches.

    Deliberately does NOT accept HTTP Basic Auth or the state-token header —
    the internal router has no Terraform-backend constraint to work around,
    and callers (drift-detector, liveness-detector) are plain Python HTTP
    clients that can send any header they like. Keeping this check separate
    from `require_state_token` is the whole point: a caller holding only
    `TERRADUCKTEL_STATE_TOKEN` (e.g. an executor container) must NOT be able
    to authenticate here.
    """
    try:
        expected = _expected_internal_token()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal token not configured",
        )
    if x_terraducktel_internal_token is not None and hmac.compare_digest(
        x_terraducktel_internal_token, expected
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing internal token",
    )
