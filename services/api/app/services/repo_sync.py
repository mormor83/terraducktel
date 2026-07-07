"""Periodic repo-path sync.

Decides whether each workspace's `tf_working_dir` still exists in the
source repo at its tracked `repo_ref`. The result lands on
`workspaces.path_status` so the dashboard can show an "orphaned" badge,
and so admins can force-delete those rows without a real terraform
destroy (the path is gone, the executor can't cd into it anyway).

Design:
- Group workspaces by (repo_url, ref) and clone each group exactly once
  per cycle. Many of our workspaces share a repo + branch, so the naive
  "one clone per workspace" would be ~Nx more expensive without changing
  the result.
- Shallow clone (depth=1). We only need to ls one directory.
- Auth: env GITHUB_TOKEN → per-BU `bu.<slug>.github.token` → legacy
  global `github.token`. Same precedence as the discovery flow so an
  operator who set up GitHub once doesn't need to set it up twice.
- Local-only workspaces (`repo_url` empty or `local://…`) are skipped.
  They have no remote to check; their `path_status` stays whatever it
  was (default 'unknown'), and they remain freely deletable as before.
- Errors during clone (auth, network) don't flip a workspace to
  'orphaned' — they leave it at its existing status and surface in the
  `RepoSyncResult.errors` list. We don't want a transient outage to
  greenlight a destructive cleanup.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.models.business_unit import BusinessUnit
from app.models.workspace import Workspace
from app.services.config_service import ConfigService
from app.services.repo_discovery import _inject_credentials, _redact_url

logger = logging.getLogger(__name__)


def _is_local(repo_url: str | None) -> bool:
    if not repo_url:
        return True
    return repo_url.startswith("local://")


@dataclass
class RepoSyncResult:
    """Outcome of one sync pass."""
    checked: int = 0
    ok: int = 0
    orphaned: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


async def _bu_slug_by_id(session: AsyncSession, bu_id: str) -> str | None:
    bu = await session.get(BusinessUnit, bu_id)
    return bu.slug if bu else None


async def _resolve_token(
    session: AsyncSession, repo_url: str, bu_slug: str | None
) -> tuple[str | None, str | None]:
    """Pick the right credentials for cloning `repo_url`.

    Returns (username, token). Both None means clone without auth — only
    works for public repos. github.com falls back to BU PAT and then to
    legacy global key, matching the discovery flow.
    """
    if "github.com" not in repo_url:
        return (None, None)
    svc = ConfigService(session, get_credential_encryption_key())
    bu_token = ""
    if bu_slug:
        bu_token = (await svc.get_for_bu(bu_slug, "github.token") or "").strip()
    token = (
        os.environ.get("GITHUB_TOKEN", "").strip()
        or bu_token
        or (await svc.get("github.token") or "").strip()
    )
    if not token:
        return (None, None)
    # GitHub conventionally accepts any non-empty username when the password
    # is a PAT; `x-access-token` is the documented choice.
    return ("x-access-token", token)


def _shallow_clone(repo_url: str, ref: str, username: str | None, token: str | None) -> tuple[str | None, str | None]:
    """Shallow clone for path-existence checks. Returns (tmpdir, error).

    The caller is responsible for cleaning up `tmpdir` when it's not None.
    """
    auth_url = _inject_credentials(repo_url, username, token)
    tmpdir = tempfile.mkdtemp(prefix="terraducktel-sync-")
    try:
        r = subprocess.run(
            ["git", "clone", "--depth=1", "--branch", ref, "--", auth_url, tmpdir],
            capture_output=True,
            text=True,
            timeout=60,
            # Restrict git to network transports only. Without this, a repo_url
            # like `ext::sh -c '...'` or `file://...` would make git execute a
            # command / read local files. GIT_ALLOW_PROTOCOL is the
            # version-independent guard; the scheme allow-list on the schema is
            # defense-in-depth on top.
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ALLOW_PROTOCOL": "http:https:ssh",
            },
        )
        if r.returncode != 0:
            err = (r.stderr or "git clone failed").strip()
            if token:
                err = err.replace(token, "***")
            return (None, err)
        return (tmpdir, None)
    except FileNotFoundError:
        return (None, "git binary not available in API container")
    except subprocess.TimeoutExpired:
        return (None, f"git clone timed out for {_redact_url(repo_url)} @ {ref}")


def _cleanup(tmpdir: str) -> None:
    import shutil

    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


async def check_workspace_paths(
    session: AsyncSession, workspaces: list[Workspace]
) -> RepoSyncResult:
    """Check `tf_working_dir` existence for each workspace and persist
    the result. Caller must `await session.commit()` afterwards."""
    res = RepoSyncResult()

    # Group by (repo_url, ref, bu_id) — the bu_id is needed so we resolve
    # the right per-BU PAT. Two workspaces in the same repo/ref but
    # different BUs would (correctly) use different tokens.
    groups: dict[tuple[str, str, str], list[Workspace]] = {}
    for ws in workspaces:
        if _is_local(ws.repo_url):
            res.skipped += 1
            continue
        key = (ws.repo_url or "", ws.repo_ref or "main", ws.business_unit_id)
        groups.setdefault(key, []).append(ws)

    for (repo_url, ref, bu_id), wss in groups.items():
        bu_slug = await _bu_slug_by_id(session, bu_id)
        username, token = await _resolve_token(session, repo_url, bu_slug)
        tmpdir, err = _shallow_clone(repo_url, ref, username, token)
        if err:
            res.errors.append(f"{_redact_url(repo_url)} @ {ref}: {err}")
            # Leave each workspace's path_status untouched on clone error.
            continue
        try:
            for ws in wss:
                # Resolve `tf_working_dir` against the clone root. An empty
                # / "." path means the repo root itself, which by
                # construction exists after a successful clone.
                rel = (ws.tf_working_dir or ".").lstrip("/")
                target = os.path.join(tmpdir, rel)
                if rel in ("", ".") or os.path.isdir(target):
                    ws.path_status = "ok"
                    res.ok += 1
                else:
                    ws.path_status = "orphaned"
                    res.orphaned += 1
                ws.path_status_checked_at = datetime.now(timezone.utc)
                res.checked += 1
        finally:
            _cleanup(tmpdir)

    return res


async def sync_workspace(session: AsyncSession, workspace_id: str) -> Workspace | None:
    """One-workspace recheck used by the manual sync endpoint."""
    ws = await session.get(Workspace, workspace_id)
    if ws is None:
        return None
    await check_workspace_paths(session, [ws])
    await session.commit()
    await session.refresh(ws)
    return ws


async def sync_all(session: AsyncSession, *, bu_id: str | None = None) -> RepoSyncResult:
    """BU-bulk (or cross-BU when bu_id is None — used by the background
    loop)."""
    stmt = select(Workspace)
    if bu_id is not None:
        stmt = stmt.where(Workspace.business_unit_id == bu_id)
    rows = (await session.execute(stmt)).scalars().all()
    res = await check_workspace_paths(session, list(rows))
    await session.commit()
    return res


# ─── Background loop ───────────────────────────────────────────────────────


_DEFAULT_POLL_SECONDS = 600  # 10 minutes


async def _get_poll_seconds(session_factory) -> int:
    """Pull the interval from runtime_config if present, else default."""
    try:
        async with session_factory() as s:
            svc = ConfigService(s, get_credential_encryption_key())
            v = await svc.get("repo_sync.poll_seconds")
            if v:
                return max(60, int(v))
    except Exception:  # noqa: BLE001 — config errors fall back to default
        pass
    return _DEFAULT_POLL_SECONDS


async def repo_sync_loop(session_factory):
    """In-process background loop. Runs forever; cancelled cleanly on
    application shutdown by the lifespan handler in main.py.

    First iteration is delayed by 60s so an API restart doesn't
    immediately pound the upstream git host. Errors inside the loop are
    logged and swallowed — a transient git outage must not kill the
    loop.
    """
    import asyncio

    await asyncio.sleep(60)
    while True:
        try:
            async with session_factory() as s:
                res = await sync_all(s)
            logger.info(
                "repo_sync: checked=%d ok=%d orphaned=%d skipped=%d errors=%d",
                res.checked, res.ok, res.orphaned, res.skipped, len(res.errors),
            )
            for e in res.errors[:5]:
                logger.info("repo_sync error: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("repo_sync iteration failed", exc_info=True)
        interval = await _get_poll_seconds(session_factory)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
