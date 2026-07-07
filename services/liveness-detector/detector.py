"""Periodic liveness checks for github-imported workspaces.

For each workspace whose `repo_url` points at github.com, verify that the
`tf_working_dir` still exists at the workspace's tracked branch. When it's gone
upstream, call the auto-delete endpoint so the workspace doesn't dangle.

The check is GitHub Contents API: `GET /repos/{owner}/{repo}/contents/{path}?ref={ref}`.
HTTP 200 = exists, 404 = missing. Other statuses (5xx, rate limit, network)
are treated as "unknown" and the workspace is left alone — better to keep a
real workspace than auto-delete on a transient outage.

Triggered_by audit: `auto_delete_orphan` with the upstream check failure as
the reason.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone

import httpx

# Grace period for freshly-imported workspaces. The first cron tick can land
# seconds after a bulk-import; if the user mis-set the branch, we don't want
# to race them and silently undo the import.
NEW_WORKSPACE_GRACE_SEC = int(os.environ.get("LIVENESS_GRACE_SEC", "600"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


_GITHUB_OWNER_REPO_RE = re.compile(
    r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/|$)"
)


def _parse_github(repo_url: str | None) -> tuple[str, str] | None:
    if not repo_url:
        return None
    if repo_url.startswith("local://"):
        return None
    m = _GITHUB_OWNER_REPO_RE.search(repo_url)
    if not m:
        return None
    return m.group(1), m.group(2)


def _check_path_exists(
    client: httpx.Client, owner: str, repo: str, path: str, ref: str, gh_token: str
) -> tuple[bool | None, int]:
    """Return (exists, status). exists=None means "unknown, leave it alone"."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "terraducktel-liveness",
    }
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    # `path` may be "." for repo-root workspaces — the contents API rejects ".",
    # so probe the repo metadata endpoint instead.
    if not path or path == "." or path == "/":
        url = f"https://api.github.com/repos/{owner}/{repo}"
        params = {}
    else:
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.strip('/')}"
        params = {"ref": ref}
    try:
        r = client.get(url, headers=headers, params=params, timeout=15)
    except httpx.RequestError:
        logger.warning("github request failed for %s/%s/%s", owner, repo, path, exc_info=True)
        return None, -1
    if r.status_code == 200:
        return True, 200
    if r.status_code == 404:
        return False, 404
    # 401/403: token is bad or rate-limited. Don't auto-delete on auth issues.
    return None, r.status_code


def _scan_once(api_url: str, internal_token: str) -> None:
    base = api_url.rstrip("/")
    # Internal-only token — deliberately not TERRADUCKTEL_STATE_TOKEN, which is
    # also handed to executor containers. See app/auth/internal_token.py.
    headers = {"X-Terraducktel-Internal-Token": internal_token}

    with httpx.Client(timeout=30.0) as client:
        # Pull workspaces from the same internal endpoint the drift detector uses.
        r = client.get(f"{base}/api/v1/internal/workspaces", headers=headers)
        if r.status_code == 401:
            logger.error("API 401 — internal token mismatch")
            return
        r.raise_for_status()
        workspaces = r.json()
        if not isinstance(workspaces, list):
            logger.error("unexpected workspaces payload: %r", workspaces)
            return

        # Fetch the github token once per scan cycle.
        gh_token = ""
        try:
            tr = client.get(f"{base}/api/v1/internal/github-token", headers=headers)
            if tr.status_code == 200:
                gh_token = (tr.json().get("token") or "").strip()
        except httpx.RequestError:
            logger.warning("could not fetch github token from API", exc_info=True)
        if not gh_token:
            logger.info("no github token available — only public repos will be checked")

        now = datetime.now(timezone.utc)
        for ws in workspaces:
            wid = ws.get("id")
            name = ws.get("name", wid)
            parsed = _parse_github(ws.get("repo_url"))
            if not parsed:
                # local://, forgejo, or no repo URL — out of scope for this cron.
                continue
            # Grace period for fresh imports — see NEW_WORKSPACE_GRACE_SEC.
            created_at = ws.get("created_at")
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    age = (now - dt).total_seconds()
                    if age < NEW_WORKSPACE_GRACE_SEC:
                        logger.info(
                            "skip %s — within grace period (%.0fs < %ss)",
                            name, age, NEW_WORKSPACE_GRACE_SEC,
                        )
                        continue
                except ValueError:
                    logger.warning("could not parse created_at for %s: %r", name, created_at)
            owner, repo = parsed
            path = ws.get("tf_working_dir", ".") or "."
            ref = ws.get("repo_ref", "main") or "main"

            exists, http_status = _check_path_exists(client, owner, repo, path, ref, gh_token)
            if exists is None:
                logger.info(
                    "skip %s (%s/%s/%s @%s) — github status %s",
                    name, owner, repo, path, ref, http_status,
                )
                continue
            if exists:
                logger.debug("alive: %s (%s/%s/%s @%s)", name, owner, repo, path, ref)
                continue

            # 404 — the dir no longer exists at this ref. Auto-delete + audit.
            reason = (
                f"github contents 404 for {owner}/{repo} path '{path}' ref '{ref}'"
            )
            logger.warning("orphan: %s — %s", name, reason)
            d = client.post(
                f"{base}/api/v1/internal/workspaces/{wid}/auto-delete",
                headers=headers,
                json={"reason": reason},
            )
            if d.status_code in (200, 204):
                logger.warning("auto-deleted orphan workspace %s (%s)", name, wid)
            else:
                logger.error(
                    "auto-delete failed for %s (%s): %s %s",
                    name, wid, d.status_code, d.text,
                )


def main() -> None:
    api_url = os.environ.get("API_URL", "http://api:8000")
    internal_token = os.environ.get("TERRADUCKTEL_INTERNAL_TOKEN", "")
    interval = int(os.environ.get("LIVENESS_INTERVAL_SEC", "300"))

    if not internal_token:
        logger.error("TERRADUCKTEL_INTERNAL_TOKEN is required")
        raise SystemExit(1)

    logger.info("Liveness detector started (interval=%ss)", interval)
    while True:
        try:
            _scan_once(api_url, internal_token)
        except Exception:
            logger.exception("liveness scan iteration failed")
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    main()
