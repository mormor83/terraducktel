"""Terraform HTTP state backend router.

Provides GET/POST for state, POST/DELETE for lock.
These endpoints are internal (used by Terraform directly).
"""
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.internal_token import StateAuth, require_state_token


def _check_state_scope(auth: StateAuth, workspace_id: str) -> None:
    """A run-scoped state token may only touch its own workspace.
    Global-scope callers (auth.workspace_id is None) are unrestricted."""
    if auth.workspace_id is not None and auth.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="State token not valid for this workspace",
        )
from app.db import get_db
from app.models.workspace import Workspace
from app.services.state_service import StateLockService
from app.services.s3_state_service import S3StateService
from app.services.secret_scanner import scan_terraform_state_json
from app.services import aws_account_service as accs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/state", tags=["state"])

_USE_LOCALSTACK = os.environ.get("S3_USE_LOCALSTACK", "false").lower() in ("true", "1", "yes")
# Fallback bucket name only used when a workspace has no configured AWS account
# (e.g. legacy workspaces created before phase-8). New workspaces should reach
# their per-account bucket via AwsAccount.state_bucket.
_FALLBACK_BUCKET = os.environ.get("S3_STATE_BUCKET", "terraducktel-state")
_S3_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


async def _service_for(ws: Workspace, db: AsyncSession) -> tuple[S3StateService, str]:
    """Return (S3 client bound to the workspace's AwsAccount, key for the leaf path).

    The state key mirrors the workspace's `tf_working_dir` exactly so the file
    layout in S3 matches the layout in git: e.g.
        account-111111111111/eu-central-1/region-shared-resources/terraform.tfstate
    Each AWS account has its own bucket — never shared.
    """
    account = await accs.get_account_by_account_id(db, ws.aws_account_id)
    if account is not None:
        # Per-account credentials always talk to real AWS — `S3_USE_LOCALSTACK`
        # only applies to the legacy env-default fallback below.
        access_key = accs.decrypt_secret(account.access_key_id_encrypted)
        secret_key = accs.decrypt_secret(account.secret_access_key_encrypted)
        svc = S3StateService(
            bucket=account.state_bucket,
            use_localstack=False,
            region=account.state_bucket_region,
            access_key_id=access_key,
            secret_access_key=secret_key,
        )
    else:
        # Backward-compat: workspace without a registered AwsAccount falls back
        # to the env-configured shared bucket (LocalStack in dev).
        svc = S3StateService(
            bucket=_FALLBACK_BUCKET, use_localstack=_USE_LOCALSTACK, region=_S3_REGION
        )
    leaf_path = (ws.tf_working_dir or ".").strip("/")
    if leaf_path in ("", "."):
        leaf_path = ws.name
    key = f"{leaf_path}/terraform.tfstate"
    return svc, key


@router.get("/{workspace_id}")
async def get_state(
    workspace_id: str,
    auth: StateAuth = Depends(require_state_token),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the latest Terraform state for a workspace.

    Returns 404 when the workspace has never been applied (NoSuchKey from S3
    yields None). This matches Terraform's HTTP backend spec: 404 = "no state
    yet, will create on first write"; 200 + empty JSON would be parsed as a
    *corrupted* state file ("does not have a 'version' attribute") and break
    plan/apply/destroy on fresh workspaces — see issue surfacing on the
    Cloudflare destroy where the run failed at terraform init.

    Real S3 errors (connectivity, permission, bucket misconfig) still surface
    as 503 — collapsing them to "empty state" would let Terraform recreate
    every existing resource on the next apply.
    """
    _check_state_scope(auth, workspace_id)
    ws = await db.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="State not found")

    try:
        svc, key = await _service_for(ws, db)
        data = svc.get_state_at(key)
    except Exception:
        logger.exception("S3 state fetch failed for workspace %s", workspace_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="State backend unavailable",
        )

    if data is None:
        raise HTTPException(status_code=404, detail="State not found")
    return Response(content=data, media_type="application/json")


@router.post("/{workspace_id}")
async def put_state(
    workspace_id: str,
    request: Request,
    auth: StateAuth = Depends(require_state_token),
    db: AsyncSession = Depends(get_db),
):
    """Upload Terraform state for a workspace."""
    _check_state_scope(auth, workspace_id)
    # Cap the state body so an unbounded upload can't exhaust memory.
    # 64 MiB is far above any realistic terraform.tfstate. Reject on the
    # declared Content-Length FIRST so a huge body is refused before it is
    # buffered into memory; then re-check the actual length as a backstop for
    # a missing/lying header.
    _MAX_STATE_BYTES = 64 * 1024 * 1024
    _declared = request.headers.get("content-length")
    if _declared is not None:
        try:
            if int(_declared) > _MAX_STATE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="State payload too large",
                )
        except ValueError:
            pass
    body = await request.body()
    if len(body) > _MAX_STATE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="State payload too large",
        )
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="State body must be valid JSON",
        )
    ok, reason = scan_terraform_state_json(data)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=reason or "State rejected due to suspected secrets",
        )

    ws = await db.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    try:
        svc, key = await _service_for(ws, db)
        svc.put_state_at(key, body)
    except Exception:
        logger.exception("Failed to persist state to S3 for workspace %s", workspace_id)
        raise HTTPException(status_code=500, detail="Failed to persist state to S3")

    return Response(status_code=200)


def _parse_lock_info(body: bytes, *, allow_empty: bool = False) -> dict:
    """Parse the Terraform HTTP backend lock-info body.

    POST /lock: the body is required and must be valid JSON (400 otherwise).
    DELETE /lock: terraform sometimes sends an empty body — tolerate it
    (`allow_empty=True`) so a "no lock info on unlock" call still works.
    """
    if not body:
        if allow_empty:
            return {}
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty lock request body",
        )
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON in lock request body",
        )
    return parsed if isinstance(parsed, dict) else {}


def _lock_info_payload(entry) -> dict:
    """Build the Terraform-style lock-info JSON for a 409 response so
    `terraform plan` / `apply` names the holder in its error output."""
    return {
        "ID": entry.run_id,
        "Operation": "tdt-run",
        "Who": entry.run_id,
        "Created": entry.acquired_at.isoformat() if entry.acquired_at else None,
        "Info": f"Held by run {entry.run_id}",
    }


@router.post("/{workspace_id}/lock")
async def lock_state(
    workspace_id: str,
    request: Request,
    auth: StateAuth = Depends(require_state_token),
    db: AsyncSession = Depends(get_db),
):
    """Lock state for a workspace (TF HTTP backend protocol)."""
    _check_state_scope(auth, workspace_id)
    lock_info = _parse_lock_info(await request.body())
    run_id = lock_info.get("ID", "unknown")

    svc = StateLockService(db)
    acquired, holder = await svc.acquire_lock(workspace_id, run_id)
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_lock_info_payload(holder) if holder else "State is already locked",
        )
    return Response(status_code=200)


@router.delete("/{workspace_id}/lock")
async def unlock_state(
    workspace_id: str,
    request: Request,
    auth: StateAuth = Depends(require_state_token),
    db: AsyncSession = Depends(get_db),
):
    """Unlock state for a workspace (TF HTTP backend protocol).

    Idempotent: a release on an already-unlocked workspace returns 200, so the
    legitimate "no-op apply" path never surfaces terraform's scary
    "Error releasing the state lock" advice. Only a genuine holder mismatch
    (caller's lock ID differs from the row's) returns 409.
    """
    _check_state_scope(auth, workspace_id)
    lock_info = _parse_lock_info(await request.body(), allow_empty=True)
    lock_id = lock_info.get("ID")

    svc = StateLockService(db)
    released, holder = await svc.release_lock(workspace_id, lock_id=lock_id)
    if not released:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_lock_info_payload(holder) if holder else "Lock ID mismatch",
        )
    return Response(status_code=200)
