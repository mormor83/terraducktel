"""Forgejo + GitHub webhook handlers with HMAC signature validation."""
import hashlib
import hmac
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.db import get_db
from app.models.business_unit import BusinessUnit
from app.models.config import Config
from app.models.run import Run, RunStatus
from app.models.workspace import Workspace
from app.services import run_step_service as steps_svc
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


async def _get_webhook_secret(db: AsyncSession) -> str:
    """Retrieve the legacy GLOBAL webhook HMAC secret from the config table.

    Used by the back-compat webhook routes (`/forgejo`, `/github`) that don't
    encode a BU in the path. Per-BU routes use `_get_bu_webhook_secret`.
    """
    row = await db.get(Config, "webhook.secret")
    # Reject an empty/whitespace secret: an empty secret makes the
    # HMAC `hmac.new(b"", body, sha256)` attacker-computable, so any request
    # body could be signed → full signature bypass. Mirror the non-empty check
    # `_get_bu_webhook_secret` already enforces.
    if row is None or not (row.value or "").strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook secret not configured",
        )
    return row.value


async def _get_bu_webhook_secret(db: AsyncSession, bu_slug: str) -> tuple[str, BusinessUnit]:
    """Read `bu.<slug>.webhook.secret`. Falls back to the legacy global
    `webhook.secret` for one release so a BU that hasn't onboarded its own
    secret yet keeps working.
    """
    bu = (
        await db.execute(select(BusinessUnit).where(BusinessUnit.slug == bu_slug))
    ).scalars().first()
    if bu is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business unit '{bu_slug}' not found",
        )
    svc = ConfigService(db, get_credential_encryption_key())
    secret = (await svc.get_for_bu(bu_slug, "webhook.secret") or "").strip()
    if not secret:
        # Allow falling back to the legacy global key during the transition.
        # Strip + re-check so a whitespace-only global secret is rejected too,
        # consistent with `_get_webhook_secret` (an empty HMAC key is forgeable).
        legacy = await db.get(Config, "webhook.secret")
        legacy_val = (legacy.value or "").strip() if legacy is not None else ""
        if not legacy_val:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Webhook secret not configured for BU '{bu_slug}'",
            )
        secret = legacy_val
    return secret, bu


def _verify_signature(payload: bytes, secret: str, signature: str | None) -> bool:
    """Verify HMAC-SHA256 signature from Forgejo."""
    if not signature:
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/forgejo", status_code=status.HTTP_202_ACCEPTED)
async def handle_forgejo_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Forgejo push/PR webhooks.

    Validates HMAC-SHA256 signature, then creates a plan run
    for the matching workspace.
    """
    body = await request.body()
    signature = request.headers.get("X-Gitea-Signature")
    event_type = request.headers.get("X-Gitea-Event", "unknown")

    # Validate signature
    secret = await _get_webhook_secret(db)
    if not _verify_signature(body, secret, signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature",
        )

    # Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    # Find matching workspace by repo name + environment derived from branch ref.
    # H6 mitigation: branch → environment mapping prevents a `dev` push from
    # triggering a `prod` plan when two workspaces share a name across envs.
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    repo_short_name = repo_full_name.split("/")[-1] if "/" in repo_full_name else repo_full_name

    ref = payload.get("ref", "") or payload.get("base_ref", "")
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref
    branch_to_env = {
        "main": "prod",
        "master": "prod",
        "production": "prod",
        "staging": "staging",
        "stage": "staging",
        "develop": "dev",
        "dev": "dev",
    }
    inferred_env = branch_to_env.get(branch, "dev")

    # Try (name, environment) first; fall back to bare name (single-env workspaces).
    result = await db.execute(
        select(Workspace).where(
            Workspace.name == repo_short_name,
            Workspace.environment == inferred_env,
        )
    )
    workspace = result.scalars().first()
    if workspace is None:
        result = await db.execute(
            select(Workspace).where(Workspace.name == repo_short_name)
        )
        rows = list(result.scalars().all())
        if len(rows) == 1:
            workspace = rows[0]
        elif len(rows) > 1:
            return {
                "status": "ignored",
                "reason": f"Ambiguous workspace match for '{repo_short_name}' (branch '{branch}'); add an env-mapped workspace.",
            }

    if workspace is None:
        return {"status": "ignored", "reason": f"No workspace matches repo '{repo_full_name}'"}

    # Same opt-in + branch-match rule as the GitHub handler.
    if not workspace.webhook_enabled:
        return {"status": "ignored", "reason": f"webhook disabled for workspace '{workspace.name}'"}
    if branch and branch != workspace.repo_ref:
        return {
            "status": "ignored",
            "reason": f"push branch '{branch}' != workspace branch '{workspace.repo_ref}'",
        }

    # Create a plan run
    run = Run(
        id=str(uuid.uuid4()),
        workspace_id=workspace.id,
        triggered_by=f"webhook:{event_type}",
        command="plan",
        status=RunStatus.PENDING,
        branch=workspace.repo_ref,
    )
    db.add(run)
    await db.flush()
    await steps_svc.seed_steps(db, run.id, "plan")
    # Enqueue the worker job — without this the Run sits in PENDING and the
    # executor is never launched. The manual trigger in routers/runs.py
    # does the same call; webhook-triggered runs need it too.
    from app.services.run_worker import enqueue_job

    await enqueue_job(db, run_id=run.id, phase="plan")
    await db.commit()

    return {"status": "accepted", "run_id": run.id, "workspace_id": workspace.id}


# ─── GitHub webhook ────────────────────────────────────────────────────────


def _verify_github_signature(payload: bytes, secret: str, signature: str | None) -> bool:
    """Verify GitHub's `X-Hub-Signature-256: sha256=...` header."""
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _changed_files(payload: dict) -> set[str]:
    """Union the added/modified/removed lists from every commit in a push event."""
    out: set[str] = set()
    for c in payload.get("commits", []) or []:
        for k in ("added", "modified", "removed"):
            for p in c.get(k, []) or []:
                if p:
                    out.add(p)
    return out


def _matches_workspace(ws: Workspace, changed: set[str]) -> bool:
    """Did this push touch any file inside this workspace's tf_working_dir?

    If the workspace is at the repo root (`tf_working_dir == "."` or empty),
    any change counts. Otherwise the file path must start with the workspace's
    dir prefix.
    """
    wd = (ws.tf_working_dir or ".").strip("/")
    if wd in ("", "."):
        return True
    return any(p.startswith(wd + "/") or p == wd for p in changed)


def _extract_repo_full_name(repo_url: str | None) -> str | None:
    """Pull the `owner/repo` pair out of a git remote URL.

    Handles the URL styles workspaces actually store: `https://host/owner/repo`,
    `https://host/owner/repo.git`, and the scp-like `git@host:owner/repo.git`.
    Returns the pair lower-cased, or None if `repo_url` doesn't look like a
    git remote (fewer than two path segments).
    """
    if not repo_url:
        return None
    url = repo_url.strip()
    if url.endswith(".git"):
        url = url[: -len(".git")]
    url = url.rstrip("/")
    if "://" not in url and "@" in url and ":" in url:
        # git@github.com:org/repo -> github.com/org/repo, so it splits on "/"
        # the same way a URL does.
        host_part, _, path_part = url.partition(":")
        url = f"{host_part.rpartition('@')[-1]}/{path_part}"
    parts = [p for p in url.split("/") if p]
    if len(parts) < 2:
        return None
    return f"{parts[-2]}/{parts[-1]}".lower()


def _repo_url_matches_full_name(repo_url: str | None, repo_full_name: str) -> bool:
    """Exact `owner/repo` match — NOT the loose substring test used to
    pre-filter the SQL query.

    `Workspace.repo_url.ilike(f"%{repo_full_name}%")` alone also
    matches unrelated repos whose name merely *contains* this repo's full
    name, e.g. a push to `org/infra` would ilike-match a workspace pointing
    at `org/infra-legacy`. If that unrelated workspace belongs to a different
    Business Unit, the false match lets one BU's push silently trigger a plan
    in another BU — exactly the cross-BU bleed the per-BU route
    (`/github/{bu_slug}`) is designed to prevent, just reached from the
    BU-agnostic legacy route. This exact check is applied in Python, after
    the DB-level ilike pre-filter, to close that gap without requiring a
    `bu_slug` in the URL.
    """
    extracted = _extract_repo_full_name(repo_url)
    return extracted is not None and extracted == repo_full_name.strip().lower()


@router.post("/github", status_code=status.HTTP_202_ACCEPTED)
async def handle_github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """GitHub `push` webhook → trigger plan(s) for affected workspaces.

    A single push can touch multiple stacks (e.g. when a developer edits files
    in `account-XXX/region/foo/dev/` and `account-XXX/region/foo/prod/` in the
    same commit). Match changed files against each workspace's `tf_working_dir`
    and create one run per match. Plans only — applies still need a manual
    approve via the UI.
    """
    body = await request.body()
    secret = await _get_webhook_secret(db)
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_github_signature(body, secret, signature):
        raise HTTPException(status_code=403, detail="Invalid GitHub signature")

    event_type = request.headers.get("X-GitHub-Event", "unknown")
    if event_type != "push":
        return {"status": "ignored", "reason": f"unsupported event '{event_type}'"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    repo_full_name = payload.get("repository", {}).get("full_name", "")
    ref = payload.get("ref", "")
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref

    if not repo_full_name:
        return {"status": "ignored", "reason": "no repository.full_name in payload"}

    # Look up every workspace whose `repo_url` references this repo. The SQL
    # ilike is a cheap, deliberately loose pre-filter so both
    # `https://github.com/org/repo.git` and `git@github.com:org/repo.git`
    # styles are candidates; `_repo_url_matches_full_name` then requires an
    # EXACT `owner/repo` match. This route has no BU in its URL (unlike
    # `/github/{bu_slug}`), so it can't restrict candidates to a single BU —
    # but an exact match still guarantees it only ever triggers workspaces
    # actually bound to the repo that pushed, never a same-substring
    # workspace living in a different BU.
    rows = (
        await db.execute(
            select(Workspace).where(Workspace.repo_url.ilike(f"%{repo_full_name}%"))
        )
    ).scalars().all()
    rows = [ws for ws in rows if _repo_url_matches_full_name(ws.repo_url, repo_full_name)]
    if not rows:
        return {
            "status": "ignored",
            "reason": f"no workspace bound to repo '{repo_full_name}'",
        }

    changed = _changed_files(payload)
    triggered: list[dict] = []
    skipped: list[dict] = []
    for ws in rows:
        # Per-workspace opt-in: webhook_enabled must be true AND the push
        # branch must match the workspace's tracked branch. This stops a push
        # to `feature/foo` from kicking off a plan on a workspace that's
        # tracking `main`.
        if not ws.webhook_enabled:
            skipped.append({"workspace": ws.name, "reason": "webhook disabled"})
            continue
        if branch and branch != ws.repo_ref:
            skipped.append({
                "workspace": ws.name,
                "reason": f"push branch '{branch}' != workspace branch '{ws.repo_ref}'",
            })
            continue
        if changed and not _matches_workspace(ws, changed):
            continue
        run = Run(
            id=str(uuid.uuid4()),
            workspace_id=ws.id,
            triggered_by=f"webhook:github:push:{branch or 'unknown'}",
            command="plan",
            status=RunStatus.PENDING,
            branch=ws.repo_ref,
        )
        db.add(run)
        await db.flush()
        await steps_svc.seed_steps(db, run.id, "plan")
        # Enqueue so the worker actually picks this up — see the Forgejo
        # handler note for why the seed_steps + commit pair is not enough.
        from app.services.run_worker import enqueue_job

        await enqueue_job(db, run_id=run.id, phase="plan")
        triggered.append({"run_id": run.id, "workspace_id": ws.id, "name": ws.name})
        logger.info(
            "github webhook: plan for workspace %s (%s) on branch %s",
            ws.name, ws.id, branch,
        )

    if not triggered:
        return {
            "status": "ignored",
            "reason": "push did not match any enabled workspace",
            "candidates": [w.name for w in rows],
            "skipped": skipped,
            "changed_files": sorted(changed),
        }

    await db.commit()
    return {"status": "accepted", "branch": branch, "triggered": triggered, "skipped": skipped}


# ─── Per-BU GitHub webhook ─────────────────────────────────────────────────


@router.post("/github/{bu_slug}", status_code=status.HTTP_202_ACCEPTED)
async def handle_github_webhook_for_bu(
    bu_slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """GitHub push webhook scoped to a specific Business Unit.

    Each BU configures its own webhook URL (`/api/v1/webhooks/github/<slug>`)
    against its own GitHub org with a BU-specific secret. The handler
    validates against `bu.<slug>.webhook.secret` and only matches workspaces
    belonging to that BU — so a push to BU-A's repo cannot drive a plan in
    BU-B even if the repo name collides.
    """
    body = await request.body()
    secret, bu = await _get_bu_webhook_secret(db, bu_slug)
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_github_signature(body, secret, signature):
        raise HTTPException(status_code=403, detail="Invalid GitHub signature")

    event_type = request.headers.get("X-GitHub-Event", "unknown")
    if event_type != "push":
        return {"status": "ignored", "reason": f"unsupported event '{event_type}'"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    repo_full_name = payload.get("repository", {}).get("full_name", "")
    ref = payload.get("ref", "")
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref
    if not repo_full_name:
        return {"status": "ignored", "reason": "no repository.full_name in payload"}

    rows = (
        await db.execute(
            select(Workspace).where(
                Workspace.business_unit_id == bu.id,
                Workspace.repo_url.ilike(f"%{repo_full_name}%"),
            )
        )
    ).scalars().all()
    # Same exact-match tightening as the legacy route: the BU
    # filter above already prevents cross-BU triggering, but an exact
    # `owner/repo` check also stops the ilike pre-filter from picking up an
    # unrelated, similarly-named repo within the *same* BU.
    rows = [ws for ws in rows if _repo_url_matches_full_name(ws.repo_url, repo_full_name)]
    if not rows:
        return {
            "status": "ignored",
            "reason": f"no workspace in BU '{bu_slug}' bound to '{repo_full_name}'",
        }

    changed = _changed_files(payload)
    triggered: list[dict] = []
    skipped: list[dict] = []
    for ws in rows:
        if not ws.webhook_enabled:
            skipped.append({"workspace": ws.name, "reason": "webhook disabled"})
            continue
        if branch and branch != ws.repo_ref:
            skipped.append({
                "workspace": ws.name,
                "reason": f"push branch '{branch}' != workspace branch '{ws.repo_ref}'",
            })
            continue
        if changed and not _matches_workspace(ws, changed):
            continue
        run = Run(
            id=str(uuid.uuid4()),
            workspace_id=ws.id,
            triggered_by=f"webhook:github:{bu_slug}:push:{branch or 'unknown'}",
            command="plan",
            status=RunStatus.PENDING,
            branch=ws.repo_ref,
        )
        db.add(run)
        await db.flush()
        await steps_svc.seed_steps(db, run.id, "plan")
        # Enqueue so the worker picks this up. Without it, the Run sits in
        # PENDING forever — the latent bug that made webhook-triggered runs
        # invisible to operators.
        from app.services.run_worker import enqueue_job

        await enqueue_job(db, run_id=run.id, phase="plan")
        triggered.append({"run_id": run.id, "workspace_id": ws.id, "name": ws.name})
        logger.info(
            "github webhook (BU %s): plan for workspace %s (%s) on branch %s",
            bu_slug, ws.name, ws.id, branch,
        )

    if not triggered:
        return {
            "status": "ignored",
            "reason": "push did not match any enabled workspace in this BU",
            "bu": bu_slug,
            "candidates": [w.name for w in rows],
            "skipped": skipped,
            "changed_files": sorted(changed),
        }

    await db.commit()
    return {
        "status": "accepted",
        "bu": bu_slug,
        "branch": branch,
        "triggered": triggered,
        "skipped": skipped,
    }
