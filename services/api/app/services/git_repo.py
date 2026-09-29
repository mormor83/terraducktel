"""Git plumbing for environment compare + promotion.

Reads: one bare cache repo per remote under the API's temp dir. Each branch is
fetched shallow (`--depth=1`) into `refs/tdt/<branch>` — so two stacks pinned to
two different branches of the same repo are read from their own branch HEADs
without two full clones. Fetches are throttled (`FETCH_TTL_SECONDS`) so a
100-pair account compare costs one fetch per (repo, branch), not 200 clones.
Files come out via `git ls-tree` + `git cat-file --batch`; nothing is checked
out.

Writes (promotion / revert): a fresh shallow clone of the target branch per
commit, so a write never races the read cache. Author = the initiating user,
committer = the TDT bot identity. Push failures are classified into
`NonFastForward` (caller re-syncs and retries once) and `PushRejected`
(protected branch / missing permission — surfaced verbatim, never retried).

Security: git only speaks network transports (`GIT_ALLOW_PROTOCOL`) so a
crafted repo_url can't make it run `ext::` commands or read local files; tokens
are injected into the URL at call time and scrubbed from every error message.
Tests widen `ALLOWED_PROTOCOLS` to `file` for a local bare-repo fixture.
"""
from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

from app.services.repo_discovery import _inject_credentials, _redact_url

logger = logging.getLogger(__name__)

ALLOWED_PROTOCOLS = "http:https:ssh"
FETCH_TTL_SECONDS = 30.0
GIT_TIMEOUT = 120
MAX_READ_BYTES = 2_000_000  # per file; larger files are reported, not read
# Paths never read from a leaf (executor artefacts / local state).
_SKIP_PARTS = (".terraform/",)

_last_fetch: dict[tuple[str, str], tuple[float, str]] = {}


class GitError(RuntimeError):
    """A git operation failed. Message is safe to show (token scrubbed)."""


class NonFastForward(GitError):
    """Push rejected because the branch moved — re-sync and retry."""


class PushRejected(GitError):
    """Push rejected for a reason a retry won't fix (protected branch, perms)."""


@dataclass
class Creds:
    username: Optional[str] = None
    token: Optional[str] = None


def _env() -> dict:
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": ALLOWED_PROTOCOLS,
        # No user/system config (hooks, credential helpers, insteadOf rewrites).
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": tempfile.gettempdir(),
    }


def _scrub(text: str, creds: Creds | None) -> str:
    if creds and creds.token:
        text = text.replace(creds.token, "***")
        text = text.replace(urllib.parse.quote(creds.token, safe=""), "***")
    return text.strip()


def _git(args: list[str], cwd: str | None, creds: Creds | None = None, env: dict | None = None,
         input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, input=input_bytes,
            timeout=GIT_TIMEOUT, env=env or _env(),
        )
    except FileNotFoundError:
        raise GitError("git binary not available in the API container") from None
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out") from None
    return r


def _check(r: subprocess.CompletedProcess, what: str, creds: Creds | None = None) -> None:
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else (r.stderr or "")
        raise GitError(f"{what}: {_scrub(err, creds) or 'failed'}")


def _auth_url(repo_url: str, creds: Creds | None) -> str:
    if not creds or not creds.token:
        return repo_url
    return _inject_credentials(repo_url, creds.username or "x-access-token", creds.token)


# ─── read cache ──────────────────────────────────────────────────────────────


def _cache_root() -> str:
    root = os.path.join(tempfile.gettempdir(), "tdt-git-cache")
    os.makedirs(root, exist_ok=True)
    return root


def _cache_dir(repo_url: str) -> str:
    return os.path.join(_cache_root(), hashlib.sha1(repo_url.encode()).hexdigest()[:20] + ".git")


@contextmanager
def _locked(path: str) -> Iterator[None]:
    """Cross-process lock so two API workers don't fetch into one cache at once."""
    with open(path + ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def fetch_branch(repo_url: str, branch: str, creds: Creds | None = None, force: bool = False) -> str:
    """Fetch `branch` (depth 1) into the cache; return its HEAD commit sha."""
    key = (repo_url, branch)
    hit = _last_fetch.get(key)
    if hit and not force and time.monotonic() - hit[0] < FETCH_TTL_SECONDS:
        return hit[1]
    d = _cache_dir(repo_url)
    with _locked(d):
        if not os.path.isdir(d):
            _check(_git(["init", "--bare", "-q", d], None), "git init")
        ref = f"refs/tdt/{branch}"
        r = _git(
            ["fetch", "--depth=1", "--no-tags", "-q", "--", _auth_url(repo_url, creds),
             f"+refs/heads/{branch}:{ref}"],
            d, creds,
        )
        if r.returncode != 0:
            err = _scrub(r.stderr.decode("utf-8", "replace"), creds)
            if "couldn't find remote ref" in err or "not found" in err.lower():
                raise GitError(f"Branch '{branch}' not found in {_redact_url(repo_url)}")
            raise GitError(f"git fetch {_redact_url(repo_url)} @ {branch}: {err or 'failed'}")
        sha = _git(["rev-parse", ref], d).stdout.decode().strip()
    _last_fetch[key] = (time.monotonic(), sha)
    return sha


def read_dir(repo_url: str, sha: str, path: str) -> dict[str, str]:
    """All text files under `path` at `sha`, keyed by path relative to `path`.

    Binary / oversized files are returned as a placeholder so they still show
    up in the file list (and are never silently dropped from a promotion).
    """
    d = _cache_dir(repo_url)
    path = path.strip("/")
    spec = f"{path}/" if path else "."
    r = _git(["ls-tree", "-r", "-z", "--long", sha, "--", spec], d)
    _check(r, f"git ls-tree {path}")
    entries: list[tuple[str, str, int]] = []
    for rec in r.stdout.decode("utf-8", "replace").split("\0"):
        if not rec:
            continue
        meta, name = rec.split("\t", 1)
        mode, typ, obj, size = meta.split()
        if typ != "blob" or any(p in name + "/" for p in _SKIP_PARTS):
            continue
        entries.append((name, obj, int(size) if size.isdigit() else 0))
    out: dict[str, str] = {}
    wanted = [(n, o) for n, o, s in entries if s <= MAX_READ_BYTES]
    for n, o, s in entries:
        if s > MAX_READ_BYTES:
            out[_rel(n, path)] = f"<file too large to compare: {s} bytes>"
    if wanted:
        batch = _git(["cat-file", "--batch"], d, input_bytes="".join(f"{o}\n" for _, o in wanted).encode())
        _check(batch, "git cat-file")
        buf = batch.stdout
        pos = 0
        for n, _o in wanted:
            nl = buf.index(b"\n", pos)
            header = buf[pos:nl].split()
            size = int(header[2])
            body = buf[nl + 1: nl + 1 + size]
            pos = nl + 1 + size + 1
            try:
                out[_rel(n, path)] = body.decode("utf-8")
            except UnicodeDecodeError:
                out[_rel(n, path)] = f"<binary file: {size} bytes>"
    return out


def _rel(name: str, base: str) -> str:
    return name[len(base) + 1:] if base and name.startswith(base + "/") else name


def is_placeholder(text: str | None) -> bool:
    return bool(text) and text.startswith("<") and (
        text.startswith("<binary file:") or text.startswith("<file too large")
    )


# ─── writes ──────────────────────────────────────────────────────────────────


@dataclass
class Identity:
    name: str
    email: str


def _commit_env(author: Identity, committer: Identity) -> dict:
    return {
        **_env(),
        "GIT_AUTHOR_NAME": author.name,
        "GIT_AUTHOR_EMAIL": author.email,
        "GIT_COMMITTER_NAME": committer.name,
        "GIT_COMMITTER_EMAIL": committer.email,
    }


def _classify_push_error(err: str) -> GitError:
    low = err.lower()
    if "non-fast-forward" in low or "fetch first" in low or "[rejected]" in low and "stale" in low:
        return NonFastForward(err)
    if "protected branch" in low or "gh006" in low or "pull request" in low:
        return PushRejected(
            "The target branch is protected and requires a pull request — "
            "direct promotion commits are not possible on it (PR mode is not supported yet). "
            f"git said: {err}"
        )
    if "permission" in low or "403" in low or "denied" in low or "authentication failed" in low:
        return PushRejected(f"The git write credential cannot push to this repository: {err}")
    return GitError(f"git push failed: {err}")


def _clone_for_write(repo_url: str, branch: str, creds: Creds | None, depth: int = 1) -> str:
    tmp = tempfile.mkdtemp(prefix="tdt-promote-")
    r = _git(
        ["clone", f"--depth={depth}", "--no-tags", "--branch", branch, "-q", "--",
         _auth_url(repo_url, creds), tmp],
        None, creds,
    )
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise GitError(f"git clone {_redact_url(repo_url)} @ {branch}: "
                       f"{_scrub(r.stderr.decode('utf-8', 'replace'), creds)}")
    return tmp


def _push(tmp: str, branch: str, creds: Creds | None) -> None:
    r = _git(["push", "-q", "origin", f"HEAD:refs/heads/{branch}"], tmp, creds)
    if r.returncode != 0:
        raise _classify_push_error(_scrub(r.stderr.decode("utf-8", "replace"), creds))


def commit_files(
    repo_url: str,
    branch: str,
    creds: Creds | None,
    files: dict[str, Optional[str]],
    message: str,
    author: Identity,
    committer: Identity,
    expected_base: Optional[str] = None,
) -> tuple[str, str]:
    """Write `files` (repo-relative path → text, None = delete) as ONE commit and push.

    Returns (base_sha, new_sha). If `expected_base` is given and the branch
    has moved past it, raises NonFastForward *before* committing, so the caller
    re-diffs against the new HEAD instead of blindly overwriting someone's
    newer change with text computed from the old one.
    """
    tmp = _clone_for_write(repo_url, branch, creds)
    try:
        base = _git(["rev-parse", "HEAD"], tmp).stdout.decode().strip()
        if expected_base and base != expected_base:
            raise NonFastForward(f"{branch} moved from {expected_base[:10]} to {base[:10]}")
        for rel, text in files.items():
            rel = rel.strip("/")
            if not rel or ".." in rel.split("/"):
                raise GitError(f"Refusing to write outside the repository: {rel!r}")
            full = os.path.join(tmp, rel)
            if text is None:
                if os.path.exists(full):
                    _check(_git(["rm", "-q", "--", rel], tmp), f"git rm {rel}")
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            _check(_git(["add", "--", rel], tmp), f"git add {rel}")
        status = _git(["status", "--porcelain"], tmp).stdout.decode().strip()
        if not status:
            raise GitError("Nothing to commit — the target already has these changes")
        _check(_git(["commit", "-q", "-F", "-"], tmp, env=_commit_env(author, committer),
                    input_bytes=message.encode()), "git commit")
        sha = _git(["rev-parse", "HEAD"], tmp).stdout.decode().strip()
        _push(tmp, branch, creds)
        return base, sha
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def revert_commits(
    repo_url: str,
    branch: str,
    creds: Creds | None,
    shas: list[str],
    message: str,
    author: Identity,
    committer: Identity,
) -> tuple[str, str]:
    """One commit on `branch` that reverts `shas` (newest first). Returns (base, new)."""
    tmp = _clone_for_write(repo_url, branch, creds, depth=50)
    try:
        base = _git(["rev-parse", "HEAD"], tmp).stdout.decode().strip()
        for sha in shas:
            if _git(["cat-file", "-e", f"{sha}^{{commit}}"], tmp).returncode != 0:
                _git(["fetch", "-q", "--deepen=1000", "origin", branch], tmp, creds)
            if _git(["cat-file", "-e", f"{sha}^{{commit}}"], tmp).returncode != 0:
                raise GitError(f"Commit {sha[:10]} is not on {branch} any more — cannot revert it")
        env = _commit_env(author, committer)
        for sha in shas:
            r = _git(["revert", "--no-commit", sha], tmp, env=env)
            if r.returncode != 0:
                raise GitError(
                    f"Revert of {sha[:10]} conflicts with later changes on {branch}: "
                    f"{r.stderr.decode('utf-8', 'replace').strip()}"
                )
        _check(_git(["commit", "-q", "-F", "-"], tmp, env=env, input_bytes=message.encode()), "git commit")
        sha = _git(["rev-parse", "HEAD"], tmp).stdout.decode().strip()
        _push(tmp, branch, creds)
        return base, sha
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def forget_fetch(repo_url: str, branch: str) -> None:
    """Drop the fetch-throttle entry so the next read sees a fresh HEAD."""
    _last_fetch.pop((repo_url, branch), None)
