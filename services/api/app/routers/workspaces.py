"""Workspace CRUD router with RBAC."""
import logging
import os
import re
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu, scoped_workspace
from app.auth.encryption_key import get_credential_encryption_key
from app.db import get_db
from app.auth.rbac import Role, require_role
from app.models.aws_account import AwsAccount
from app.models.workspace import Workspace
from app.models.user import User
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

# Match `github.com/owner/repo` or `github.com:owner/repo` (ssh) and extract.
# Stops on `.git`, `/`, or whitespace so `https://github.com/org/repo.git/`
# returns owner=org, repo=repo without the trailing slash or `.git`.
_GITHUB_OWNER_REPO_RE = re.compile(
    r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/|$)"
)

# Azure leaves live under `azure/subscription-<guid>/<region>/<stack>` — the
# subscription is encoded in the path (like the AWS account is for AWS leaves),
# so we derive + auto-link it on import instead of asking the user to pick one.
_AZURE_SUB_RE = re.compile(r"^subscription-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")


async def _commit_or_conflict(
    db: AsyncSession,
    *,
    account: str | None = None,
    region: str | None = None,
    environment: str | None = None,
    path: str | None = None,
    detail: str | None = None,
) -> None:
    """Commit a workspace insert/update, mapping the identity-tuple unique
    violation to a clean 409 instead of a raw 500 + stack trace.

    A workspace is unique per BU on (aws_account_id, region, environment,
    tf_working_dir) — `uq_workspaces_bu_acc_region_env_path`. CLI/automation
    callers (now that admin API keys can create workspaces) hit this far more
    often than UI users, so a friendly 409 is the right failure mode. Pass
    `detail` directly (instead of account/region/environment/path) when the
    commit covers more than one workspace, e.g. a bulk import.
    """
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail or (
                f"A workspace already exists in this Business Unit for "
                f"{account}/{region}/{environment} at path '{path}'. Account + "
                f"region + environment + path must be unique per Business Unit."
            ),
        )


def _azure_sub_guid_from_path(path: str) -> str | None:
    """Extract the Azure subscription GUID from an `azure/subscription-<guid>/…` path."""
    parts = [p for p in (path or "").split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "azure":
        m = _AZURE_SUB_RE.match(parts[1])
        if m:
            return m.group(1)
    return None
from app.schemas.workspace import (
    BulkImportRequest,
    BulkImportResult,
    DiscoveryAccountOut,
    DiscoveryRequest,
    DiscoveryResultOut,
    StackCandidateOut,
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from app.services import repo_discovery
from app.services import api_key_service

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """List workspaces, scoped to the caller's selected Business Unit.

    Superadmin with no `X-Business-Unit` header (or `all`) sees every workspace
    across all BUs.
    """
    stmt = select(Workspace)
    if bu.bu_id is not None:
        stmt = stmt.where(Workspace.business_unit_id == bu.bu_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Get a single workspace. Requires viewer+ role and BU membership."""
    return await scoped_workspace(workspace_id, bu, db)


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Create a workspace. Requires admin role.

    The workspace is stamped with the currently-scoped BU. We also validate
    that the linked AWS account belongs to the same BU — cross-BU links would
    let one BU drive runs against another BU's account.
    """
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set X-Business-Unit header to a specific BU when creating a workspace",
        )
    if body.kind == "helm":
        # Helm workspaces target a cluster, not an AWS account. Require a
        # cluster_id that belongs to this BU; the aws_account_id column (NOT
        # NULL) gets the "global" sentinel used elsewhere for non-AWS rows.
        if not body.cluster_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="helm workspaces require a cluster_id",
            )
        from app.services import cluster_service

        cluster = await cluster_service.get_cluster(db, body.cluster_id, bu.bu_id)
        if cluster is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cluster {body.cluster_id} is not configured in this business unit",
            )
        effective_account = body.aws_account_id or "global"
    else:
        if not body.aws_account_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="aws_account_id is required for terraform workspaces",
            )
        acc = (
            await db.execute(
                select(AwsAccount).where(
                    AwsAccount.account_id == body.aws_account_id,
                    AwsAccount.business_unit_id == bu.bu_id,
                )
            )
        ).scalars().first()
        if acc is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"AWS account {body.aws_account_id} is not configured in this business unit"
                ),
            )
        effective_account = body.aws_account_id
    # Optional Azure subscription link: must belong to the same BU.
    azure_sub_pk: str | None = None
    if body.azure_subscription_id:
        from app.models.azure_subscription import AzureSubscription

        sub = await db.get(AzureSubscription, body.azure_subscription_id)
        if sub is None or sub.business_unit_id != bu.bu_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Azure subscription {body.azure_subscription_id} is not configured "
                    f"in this business unit"
                ),
            )
        azure_sub_pk = sub.id

    ws = Workspace(
        id=str(uuid.uuid4()),
        business_unit_id=bu.bu_id,
        name=body.name,
        environment=body.environment,
        aws_account_id=effective_account,
        region=body.region,
        repo_url=body.repo_url,
        tf_working_dir=body.tf_working_dir,
        repo_ref=body.repo_ref,
        webhook_enabled=body.webhook_enabled,
        kind=body.kind,
        cluster_id=body.cluster_id,
        azure_subscription_id=azure_sub_pk,
    )
    db.add(ws)
    await _commit_or_conflict(
        db,
        account=effective_account,
        region=body.region,
        environment=body.environment,
        path=body.tf_working_dir,
    )
    await db.refresh(ws)
    return ws


@router.put("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdate,
    request: Request,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Update a workspace. Requires operator+ role and BU membership.

    `state_aws_account_id` is an override for state-backend creds (see
    Workspace model). When set to a non-empty value it MUST reference an
    AwsAccount row in the same BU — otherwise the executor's cred lookup
    silently falls back to legacy global creds and the operator never
    finds out they got the value wrong. Empty string clears the override.
    """
    # Reconfiguring a workspace (repo, branch, state creds) is an admin action.
    # Only an `admin`-tier key may do it — and the allowlist still confines it
    # to its workspaces. Lower tiers and interactive non-admins are rejected.
    api_key_service.enforce(request, need="admin", workspace_id=workspace_id)
    ws = await scoped_workspace(workspace_id, bu, db)

    update_data = body.model_dump(exclude_unset=True)

    state_override = update_data.get("state_aws_account_id")
    if state_override is not None and state_override != "":
        # Validate against the workspace's own BU. We don't allow pointing
        # at an account registered in a DIFFERENT BU even for a superadmin —
        # that would break BU scoping for the run + audit.
        acc = (
            await db.execute(
                select(AwsAccount).where(
                    AwsAccount.account_id == state_override,
                    AwsAccount.business_unit_id == ws.business_unit_id,
                )
            )
        ).scalars().first()
        if acc is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"state_aws_account_id '{state_override}' is not a registered "
                    f"AWS account in this Business Unit. Register it in "
                    f"Settings → Cloud Providers before using it as a state-backend "
                    f"credential override."
                ),
            )
    elif state_override == "":
        # Explicit clear → store NULL so the executor falls back to aws_account_id.
        update_data["state_aws_account_id"] = None

    # azure_subscription_id: same semantics as state_aws_account_id — empty
    # string clears, a value must reference a sub in the same BU.
    az_override = update_data.get("azure_subscription_id")
    if az_override:
        from app.models.azure_subscription import AzureSubscription

        sub = await db.get(AzureSubscription, az_override)
        if sub is None or sub.business_unit_id != ws.business_unit_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"azure_subscription_id '{az_override}' is not a registered "
                    f"Azure subscription in this Business Unit."
                ),
            )
    elif az_override == "":
        update_data["azure_subscription_id"] = None

    # aws_account_id: create validates this against the BU, but update used to
    # blind-setattr it — letting an operator point their workspace at an account
    # registered only in ANOTHER BU. Validate it the same way create
    # does so state/credential resolution can't be steered cross-tenant.
    acct_override = update_data.get("aws_account_id")
    if acct_override:
        acc = (
            await db.execute(
                select(AwsAccount).where(
                    AwsAccount.account_id == acct_override,
                    AwsAccount.business_unit_id == ws.business_unit_id,
                )
            )
        ).scalars().first()
        if acc is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"aws_account_id '{acct_override}' is not a registered AWS "
                    f"account in this Business Unit."
                ),
            )

    for field, value in update_data.items():
        setattr(ws, field, value)

    await _commit_or_conflict(
        db,
        account=ws.aws_account_id,
        region=ws.region,
        environment=ws.environment,
        path=ws.tf_working_dir,
    )
    await db.refresh(ws)
    return ws


@router.post("/discover", response_model=DiscoveryResultOut)
async def discover_repo(
    body: DiscoveryRequest,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Discover Terraform stacks from either a Git repo or a local mount.

    - Remote: `repo_url` (+ optional `username`/`token` for private repos)
    - Local:  `local_path` resolved against `TERRADUCKTEL_LOCAL_REPOS_DIR` (dev only)

    If `token` is omitted and the repo is on github.com, fall back to the
    GitHub PAT configured in Settings → GitHub. Without that fallback, every
    discovery against a private github repo would otherwise fail with
    "terminal prompts disabled" because git refuses to interactively prompt.

    Each leaf folder containing `*.tf` becomes a candidate workspace with its
    own tfstate.
    """
    if body.local_path:
        result = repo_discovery.discover_local_path(body.local_path)
    elif body.repo_url:
        username = body.username
        token = body.token
        if not token and "github.com" in (body.repo_url or ""):
            svc = ConfigService(db, get_credential_encryption_key())
            # BU-scoped first (with global fallback for one release); operator
            # discovers against their currently-selected BU's PAT.
            bu_token = ""
            if bu.slug is not None:
                bu_token = (await svc.get_for_bu(bu.slug, "github.token") or "").strip()
            configured = (os.environ.get("GITHUB_TOKEN", "").strip()
                          or bu_token
                          or (await svc.get("github.token") or "").strip())
            if configured:
                token = configured
                # GitHub accepts any non-empty username when the password is a
                # PAT — `x-access-token` is the conventional one.
                username = username or "x-access-token"
        result = repo_discovery.discover_remote(
            body.repo_url,
            ref=body.ref,
            username=username,
            token=token,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either repo_url or local_path",
        )
    # Mark candidates that are already workspaces in the current BU so the UI
    # can gray them out + uncheck them by default. Without this, every
    # re-discovery shows the existing workspaces as if they were new and the
    # operator has to remember which they already imported (and the backend
    # then silently skips them on bulk-import). Scope by BU so a path that
    # belongs to BU-A's import doesn't suppress the same path for BU-B.
    existing_paths: set[str] = set()
    if bu.bu_id is not None:
        rows = (
            await db.execute(
                select(Workspace.tf_working_dir).where(
                    Workspace.business_unit_id == bu.bu_id,
                )
            )
        ).all()
        existing_paths = {r[0] for r in rows if r[0]}

    accounts_out = []
    for acc in result.accounts:
        regions_out: dict[str, list[StackCandidateOut]] = {}
        for region, stacks in acc.regions.items():
            regions_out[region] = [
                StackCandidateOut(
                    path=s.path,
                    name=s.name,
                    aws_account_id=s.aws_account_id,
                    region=s.region,
                    suggested_environment=s.suggested_environment,
                    has_tf=s.has_tf,
                    kind=s.kind,
                    already_imported=s.path in existing_paths,
                )
                for s in stacks
            ]
        accounts_out.append(DiscoveryAccountOut(aws_account_id=acc.aws_account_id, regions=regions_out))
    return DiscoveryResultOut(
        repo_url=result.repo_url,
        ref=result.ref,
        accounts=accounts_out,
        stack_count=len(result.stacks),
        errors=result.errors,
    )


@router.post("/import", response_model=BulkImportResult, status_code=status.HTTP_201_CREATED)
async def bulk_import(
    body: BulkImportRequest,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-create workspaces from a discovery result.

    Each entry becomes one workspace with `tf_working_dir = entry.path`,
    giving each leaf folder its own isolated tfstate at
    Each imported workspace gets `name = leaf folder name` and a
    `state_key = {account}/{region}/{env}/{intermediate}/{leaf}` so its S3
    state file lives at a path-unique key — two stacks named `foo` in
    `cust01/foo` and `cust02/foo` won't share state. Duplicates (same path within
    BU) are skipped.

    All created workspaces are stamped with the currently-scoped Business Unit.
    """
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set X-Business-Unit header to a specific BU when importing workspaces",
        )
    # Prefetch the BU's Azure subscriptions so azure leaves auto-link by the
    # subscription GUID embedded in their path — no manual picker needed.
    from app.models.azure_subscription import AzureSubscription

    az_by_guid = {
        a.subscription_id: a.id
        for a in (
            await db.execute(
                select(AzureSubscription).where(AzureSubscription.business_unit_id == bu.bu_id)
            )
        ).scalars().all()
    }

    created: list[Workspace] = []
    skipped: list[dict] = []
    for entry in body.entries:
        # Path is the canonical identity now (uq_workspaces_bu_acc_region_env_path).
        # Dedup by tf_working_dir within (bu, account, region, env). Name alone
        # is no longer unique — two BUs / two parent folders can share a leaf.
        existing = await db.execute(
            select(Workspace).where(
                Workspace.business_unit_id == bu.bu_id,
                Workspace.aws_account_id == entry.aws_account_id,
                Workspace.region == entry.region,
                Workspace.environment == entry.environment,
                Workspace.tf_working_dir == entry.path,
            )
        )
        if existing.scalars().first() is not None:
            skipped.append({"path": entry.path, "reason": "already exists"})
            continue
        # Derive a path-unique state_key from the entry. Mirrors _classify in
        # repo_discovery.py — duplicated here so the route doesn't depend on
        # re-running discovery's regex parsing.
        path_parts = [p for p in entry.path.split("/") if p]
        leaf_parts = path_parts[2:] if len(path_parts) >= 2 else path_parts
        state_key = "/".join(
            [entry.aws_account_id, entry.region, entry.environment, *leaf_parts]
        )
        ws = Workspace(
            id=str(uuid.uuid4()),
            business_unit_id=bu.bu_id,
            name=entry.name,
            environment=entry.environment,
            aws_account_id=entry.aws_account_id,
            region=entry.region,
            repo_url=body.repo_url,
            tf_working_dir=entry.path,
            state_key=state_key,
            kind=entry.kind,
            cluster_id=entry.cluster_id,
            # Persist the branch the discovery ran against so subsequent runs
            # (and the liveness cron) check the same ref. Defaulting to 'main'
            # caused fresh imports to be auto-deleted when the discovered path
            # only exists on a non-main branch.
            repo_ref=body.ref or "main",
        )
        # Auto-link the Azure subscription from the path (azure/subscription-<guid>/…),
        # mirroring how AWS leaves derive their account. Unmatched GUIDs (subscription
        # not registered in this BU yet) just stay unlinked — import never fails on it.
        _guid = _azure_sub_guid_from_path(entry.path)
        if _guid and _guid in az_by_guid:
            ws.azure_subscription_id = az_by_guid[_guid]
        db.add(ws)
        created.append(ws)
    if created:
        await _commit_or_conflict(
            db,
            detail=(
                "One or more workspaces in this import batch already exist in "
                "this Business Unit (duplicate account/region/environment/path). "
                "Re-run the import to pick up only the new entries."
            ),
        )
        for ws in created:
            await db.refresh(ws)
    return BulkImportResult(
        created=[WorkspaceResponse.model_validate(w) for w in created],
        skipped=skipped,
    )


@router.get("/{workspace_id}/branches")
async def list_workspace_branches(
    workspace_id: str,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Return the GitHub branches for a workspace's repo.

    Returns `{source, default_branch, branches}`:
    - `source="github"`: real branch list fetched from `repos/{owner}/{repo}/branches`.
    - `source="none"`:   no token configured, repo is local/forgejo, or the API
                         call failed. UI falls back to free-text branch input.
    Always 200; the popup decides how to render based on `source`.
    """
    ws = await scoped_workspace(workspace_id, bu, db)

    repo_url = ws.repo_url or ""
    match = _GITHUB_OWNER_REPO_RE.search(repo_url)
    if not match:
        return {
            "source": "none",
            "default_branch": ws.repo_ref,
            "branches": [],
            "message": "Repo URL is not on github.com — type a branch name manually.",
        }
    owner, repo = match.group(1), match.group(2)

    svc = ConfigService(db, get_credential_encryption_key())
    # BU-scoped first (with fallback to legacy global key) so each BU drives
    # branch lookups against its own GitHub credentials.
    bu_slug = None
    if ws.business_unit_id:
        from app.models.business_unit import BusinessUnit
        bu_row = await db.get(BusinessUnit, ws.business_unit_id)
        bu_slug = bu_row.slug if bu_row else None
    bu_token = ""
    if bu_slug:
        bu_token = (await svc.get_for_bu(bu_slug, "github.token") or "").strip()
    token = (os.environ.get("GITHUB_TOKEN", "").strip()
             or bu_token
             or (await svc.get("github.token") or "").strip())
    if not token:
        return {
            "source": "none",
            "default_branch": ws.repo_ref,
            "branches": [],
            "message": "No GitHub token configured (Settings → GitHub).",
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "terraducktel",
    }
    branches: list[str] = []
    default_branch = ws.repo_ref
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Repo metadata for the default branch (used to highlight it in the picker).
            meta = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}", headers=headers,
            )
            if meta.status_code == 200:
                default_branch = meta.json().get("default_branch") or default_branch
            elif meta.status_code in (401, 403, 404):
                return {
                    "source": "none",
                    "default_branch": ws.repo_ref,
                    "branches": [],
                    "message": f"GitHub returned {meta.status_code} for {owner}/{repo} — token may lack access.",
                }
            # Paginate branches (100/page; cap at 5 pages = 500 branches).
            for page in range(1, 6):
                r = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/branches",
                    headers=headers,
                    params={"per_page": 100, "page": page},
                )
                if r.status_code != 200:
                    break
                page_data = r.json()
                if not isinstance(page_data, list) or not page_data:
                    break
                branches.extend(b.get("name", "") for b in page_data if b.get("name"))
                if len(page_data) < 100:
                    break
    except httpx.RequestError:
        logger.exception("github branches fetch failed for %s/%s", owner, repo)
        return {
            "source": "none",
            "default_branch": ws.repo_ref,
            "branches": [],
            "message": "Could not reach GitHub.",
        }
    return {
        "source": "github",
        "default_branch": default_branch,
        "branches": sorted(set(branches)),
    }


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    force: bool = False,
    delete_state: bool = False,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Delete a workspace and all dependent rows.

    Git-synced workspaces are refused (409) by default — they would
    normally be recreated by the next discovery pass. Pass `?force=true`
    to override this, which is the path used for orphaned workspaces
    (path was renamed/removed in the source repo) where the row needs
    cleanup but a real terraform destroy isn't possible. `?delete_state=true`
    also removes the S3 tfstate prefix; default is to retain it so the
    workspace can be recovered by re-importing the path. Admin-only.

    The schema doesn't have ON DELETE CASCADE on `runs.workspace_id` and
    friends, so we explicitly clean up children: runs (and their
    run_artifacts, run_steps), drift reports, and state locks.
    Idempotent on missing children.
    """
    from sqlalchemy import delete as sql_delete

    from app.models.run import Run, RunArtifact
    from app.models.run_step import RunStep
    from app.models.drift_report import DriftReport
    from app.models.state_lock import StateLockEntry

    ws = await scoped_workspace(workspace_id, bu, db)

    repo_url = (ws.repo_url or "").strip()
    is_git_synced = bool(repo_url) and not repo_url.startswith("local://")
    if is_git_synced and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                "This workspace is synced from a Git repository and is managed "
                "automatically. Remove the Terraform module from the source "
                "repo (or pass ?force=true if the path is orphaned) instead "
                "of deleting it here."
            ),
        )

    # S3 state cleanup. Best-effort — a transient S3 outage logs but does
    # not block the DB delete. The opposite ordering would risk leaving
    # the workspace row pointing at a state we can't fetch.
    state_deleted = False
    if delete_state:
        try:
            from app.routers.state import _service_for as _state_svc_for

            svc, key = await _state_svc_for(ws, db)
            state_deleted = svc.delete_state_at(key)
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                "tfstate delete failed for workspace %s; row will still be removed",
                workspace_id, exc_info=True,
            )

    # Children first (no FK cascade in the schema yet).
    run_ids = (
        await db.execute(select(Run.id).where(Run.workspace_id == workspace_id))
    ).scalars().all()
    if run_ids:
        await db.execute(sql_delete(RunArtifact).where(RunArtifact.run_id.in_(run_ids)))
        await db.execute(sql_delete(RunStep).where(RunStep.run_id.in_(run_ids)))
        await db.execute(sql_delete(Run).where(Run.workspace_id == workspace_id))
    await db.execute(sql_delete(DriftReport).where(DriftReport.workspace_id == workspace_id))
    await db.execute(sql_delete(StateLockEntry).where(StateLockEntry.workspace_id == workspace_id))

    await db.delete(ws)
    await db.commit()
    # Audit so a force-delete is always traceable.
    if is_git_synced and force:
        from app.services.approval_service import _write_audit

        async with __import__("app.db", fromlist=["AsyncSessionLocal"]).AsyncSessionLocal() as audit_s:
            await _write_audit(
                audit_s,
                user_id=current_user.id,
                action="workspace.force_delete",
                resource_type="workspace",
                resource_id=workspace_id,
                workspace_id=None,
                details={
                    "repo_url": repo_url,
                    "tf_working_dir": ws.tf_working_dir,
                    "path_status_at_delete": ws.path_status,
                    "state_deleted": state_deleted,
                },
            )
            await audit_s.commit()


# ─── Repo sync (orphan detection) ───────────────────────────────────────────


from pydantic import BaseModel as _BaseModel


class _SyncResultOut(_BaseModel):
    checked: int
    ok: int
    orphaned: int
    skipped: int
    errors: list[str]


class _WorkspaceSyncOut(_BaseModel):
    id: str
    path_status: str
    path_status_checked_at: str | None = None


@router.post("/{workspace_id}/sync", response_model=_WorkspaceSyncOut)
async def sync_one_workspace(
    workspace_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Re-check a single workspace's path against its tracked ref.

    Useful right after renaming/removing a path in the repo — instead of
    waiting up to ~10 minutes for the background loop to catch up.
    """
    from app.services.repo_sync import sync_workspace

    # Enforce BU scope before touching the workspace (404 cross-BU).
    await scoped_workspace(workspace_id, bu, db)
    ws = await sync_workspace(db, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return _WorkspaceSyncOut(
        id=ws.id,
        path_status=ws.path_status,
        path_status_checked_at=(
            ws.path_status_checked_at.isoformat() if ws.path_status_checked_at else None
        ),
    )


@router.post("/sync", response_model=_SyncResultOut)
async def sync_all_workspaces(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Re-check every workspace in the current BU's path status.

    Same operation as the background loop, just on demand. Cross-BU
    sync is intentionally not exposed here — that path is the loop's.
    """
    from app.services.repo_sync import sync_all

    res = await sync_all(db, bu_id=bu.bu_id)
    return _SyncResultOut(
        checked=res.checked,
        ok=res.ok,
        orphaned=res.orphaned,
        skipped=res.skipped,
        errors=res.errors,
    )


# ─── State-lock inspection / force-release (operator escape hatch) ──────────


class _StateLockStatus(_BaseModel):
    held: bool
    run_id: str | None = None
    acquired_at: str | None = None


@router.get("/{workspace_id}/state-lock", response_model=_StateLockStatus)
async def get_state_lock(
    workspace_id: str,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Report whether a terraform state lock is currently held for this
    workspace, and by which run."""
    from app.models.state_lock import StateLockEntry

    await scoped_workspace(workspace_id, bu, db)
    entry = await db.get(StateLockEntry, workspace_id)
    if entry is None:
        return _StateLockStatus(held=False)
    return _StateLockStatus(
        held=True,
        run_id=entry.run_id,
        acquired_at=entry.acquired_at.isoformat() if entry.acquired_at else None,
    )


@router.delete("/{workspace_id}/state-lock", status_code=status.HTTP_204_NO_CONTENT)
async def force_release_state_lock(
    workspace_id: str,
    request: Request,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Force-release a stuck terraform state lock for this workspace.

    Use only when certain no executor is actually running against the
    workspace — releasing a lock under a live apply could let a second
    concurrent run race state. Audited.
    """
    # Force-unlocking is a destructive recovery action — gate it at the `admin`
    # key tier (allowlist-scoped), not open to plan/apply automation.
    api_key_service.enforce(request, need="admin", workspace_id=workspace_id)
    from app.services.state_service import release_workspace_lock
    from app.services.approval_service import _write_audit

    await scoped_workspace(workspace_id, bu, db)
    held_before = await release_workspace_lock(db, workspace_id)
    await db.commit()

    async with __import__("app.db", fromlist=["AsyncSessionLocal"]).AsyncSessionLocal() as audit_s:
        await _write_audit(
            audit_s,
            user_id=current_user.id,
            action="workspace.force_unlock",
            resource_type="workspace",
            resource_id=workspace_id,
            workspace_id=workspace_id,
            details={"was_held": held_before},
        )
        await audit_s.commit()
