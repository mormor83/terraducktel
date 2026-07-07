"""Unit coverage for repo_sync: token resolution, shallow clone, path checks,
the manual/bulk sync entrypoints, and the background loop (subprocess + asyncio
mocked, no real git)."""
import asyncio
import subprocess

import pytest

from app.services import repo_sync as rsync
from app.models.business_unit import DEFAULT_BU_ID
from app.models.workspace import Workspace


pytestmark = pytest.mark.usefixtures("default_bu")


# ─── _is_local ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [(None, True), ("", True), ("local://x", True), ("https://github.com/o/r", False)],
)
def test_is_local(url, expected):
    assert rsync._is_local(url) is expected


# ─── _resolve_token ──────────────────────────────────────────────────────────


async def test_resolve_token_non_github_is_unauthed(db_session):
    assert await rsync._resolve_token(db_session, "https://gitlab.com/o/r", "default") == (None, None)


async def test_resolve_token_github_env_wins(db_session, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-tok")
    assert await rsync._resolve_token(db_session, "https://github.com/o/r", None) == (
        "x-access-token",
        "env-tok",
    )


async def test_resolve_token_github_bu_then_global(db_session, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    from app.services.config_service import ConfigService
    from app.auth.encryption_key import get_credential_encryption_key

    svc = ConfigService(db_session, get_credential_encryption_key())
    await svc.set_for_bu("default", "github.token", "bu-tok")
    user, tok = await rsync._resolve_token(db_session, "https://github.com/o/r", "default")
    assert (user, tok) == ("x-access-token", "bu-tok")


async def test_resolve_token_github_no_token(db_session, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert await rsync._resolve_token(db_session, "https://github.com/o/r", None) == (None, None)


# ─── _shallow_clone ──────────────────────────────────────────────────────────


def _fake_completed(returncode, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def test_shallow_clone_success(monkeypatch):
    monkeypatch.setattr(rsync.tempfile, "mkdtemp", lambda prefix="": "/tmp/clone")
    monkeypatch.setattr(rsync.subprocess, "run", lambda *a, **k: _fake_completed(0))
    tmpdir, err = rsync._shallow_clone("https://github.com/o/r", "main", "x", "tok")
    assert tmpdir == "/tmp/clone" and err is None


def test_shallow_clone_failure_redacts_token(monkeypatch):
    monkeypatch.setattr(rsync.tempfile, "mkdtemp", lambda prefix="": "/tmp/clone")
    monkeypatch.setattr(
        rsync.subprocess, "run", lambda *a, **k: _fake_completed(128, "fatal: auth tok leaked")
    )
    tmpdir, err = rsync._shallow_clone("https://github.com/o/r", "main", "x", "tok")
    assert tmpdir is None
    assert "tok" not in err and "***" in err


def test_shallow_clone_missing_git(monkeypatch):
    monkeypatch.setattr(rsync.tempfile, "mkdtemp", lambda prefix="": "/tmp/clone")

    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(rsync.subprocess, "run", boom)
    tmpdir, err = rsync._shallow_clone("u", "main", None, None)
    assert tmpdir is None and "git binary not available" in err


def test_shallow_clone_timeout(monkeypatch):
    monkeypatch.setattr(rsync.tempfile, "mkdtemp", lambda prefix="": "/tmp/clone")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=60)

    monkeypatch.setattr(rsync.subprocess, "run", boom)
    tmpdir, err = rsync._shallow_clone("https://github.com/o/r", "main", None, None)
    assert tmpdir is None and "timed out" in err


# ─── _cleanup ────────────────────────────────────────────────────────────────


def test_cleanup_swallows_errors(monkeypatch):
    import shutil

    calls = []
    monkeypatch.setattr(shutil, "rmtree", lambda p, ignore_errors=True: calls.append(p))
    rsync._cleanup("/tmp/x")
    assert calls == ["/tmp/x"]


def test_cleanup_swallows_rmtree_exception(monkeypatch):
    import shutil

    def boom(p, ignore_errors=True):
        raise OSError("device busy")

    monkeypatch.setattr(shutil, "rmtree", boom)
    rsync._cleanup("/tmp/x")  # must not raise


# ─── check_workspace_paths ───────────────────────────────────────────────────


def _ws(name, tf_dir, repo="https://github.com/o/r", ref="main"):
    return Workspace(
        business_unit_id=DEFAULT_BU_ID,
        name=name,
        aws_account_id="123456789012",
        region="us-east-1",
        environment="dev",
        repo_url=repo,
        repo_ref=ref,
        tf_working_dir=tf_dir,
    )


async def test_check_paths_skips_local(db_session):
    res = await rsync.check_workspace_paths(db_session, [_ws("a", "x", repo="local://r")])
    assert res.skipped == 1 and res.checked == 0


async def test_check_paths_clone_error_leaves_status(db_session, monkeypatch):
    monkeypatch.setattr(rsync, "_shallow_clone", lambda *a, **k: (None, "boom"))
    ws = _ws("a", "envs/dev/vpc")
    res = await rsync.check_workspace_paths(db_session, [ws])
    assert res.errors and res.checked == 0
    assert ws.path_status is None  # untouched (transient model default)


async def test_check_paths_marks_ok_and_orphaned(db_session, monkeypatch):
    monkeypatch.setattr(rsync, "_shallow_clone", lambda *a, **k: ("/tmp/clone", None))
    monkeypatch.setattr(rsync, "_cleanup", lambda d: None)
    # root path always ok; "present" dir ok; "gone" dir orphaned.
    monkeypatch.setattr(rsync.os.path, "isdir", lambda p: p.endswith("present"))
    root = _ws("root", ".")
    present = _ws("present", "present")
    gone = _ws("gone", "gone")
    res = await rsync.check_workspace_paths(db_session, [root, present, gone])
    assert (root.path_status, present.path_status, gone.path_status) == ("ok", "ok", "orphaned")
    assert res.ok == 2 and res.orphaned == 1 and res.checked == 3


# ─── sync_workspace / sync_all ───────────────────────────────────────────────


async def test_sync_workspace_not_found(db_session):
    assert await rsync.sync_workspace(db_session, "nope") is None


async def test_sync_workspace_found(db_session, monkeypatch):
    async def fake_check(session, wss):
        for w in wss:
            w.path_status = "ok"
        return rsync.RepoSyncResult(checked=len(wss))

    monkeypatch.setattr(rsync, "check_workspace_paths", fake_check)
    ws = _ws("a", "envs/dev/vpc")
    db_session.add(ws)
    await db_session.commit()
    out = await rsync.sync_workspace(db_session, ws.id)
    assert out is not None and out.path_status == "ok"


async def test_sync_all_filters_by_bu(db_session, monkeypatch):
    captured = {}

    async def fake_check(session, wss):
        captured["n"] = len(wss)
        return rsync.RepoSyncResult(checked=len(wss))

    monkeypatch.setattr(rsync, "check_workspace_paths", fake_check)
    db_session.add(_ws("a", "x"))
    await db_session.commit()
    await rsync.sync_all(db_session, bu_id=DEFAULT_BU_ID)
    assert captured["n"] == 1
    await rsync.sync_all(db_session)  # cross-BU branch
    assert captured["n"] >= 1


# ─── _get_poll_seconds ───────────────────────────────────────────────────────


async def test_get_poll_seconds_default(_setup_db):
    assert await rsync._get_poll_seconds(_setup_db) == rsync._DEFAULT_POLL_SECONDS


async def test_get_poll_seconds_clamps_floor(_setup_db):
    async with _setup_db() as s:
        from app.services.config_service import ConfigService
        from app.auth.encryption_key import get_credential_encryption_key

        await ConfigService(s, get_credential_encryption_key()).set("repo_sync.poll_seconds", "5")
        await s.commit()
    assert await rsync._get_poll_seconds(_setup_db) == 60


async def test_get_poll_seconds_reads_value(_setup_db):
    async with _setup_db() as s:
        from app.services.config_service import ConfigService
        from app.auth.encryption_key import get_credential_encryption_key

        await ConfigService(s, get_credential_encryption_key()).set("repo_sync.poll_seconds", "900")
        await s.commit()
    assert await rsync._get_poll_seconds(_setup_db) == 900


async def test_get_poll_seconds_swallows_errors():
    def bad_factory():
        raise RuntimeError("db down")

    assert await rsync._get_poll_seconds(bad_factory) == rsync._DEFAULT_POLL_SECONDS


# ─── repo_sync_loop ──────────────────────────────────────────────────────────


async def test_loop_one_iteration_then_cancel(_setup_db, monkeypatch):
    sleeps = {"n": 0}

    async def fake_sleep(_):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:  # initial 60s ok; cancel on the interval sleep
            raise asyncio.CancelledError()

    async def fake_sync_all(s):
        return rsync.RepoSyncResult(checked=1, ok=1, errors=["e1"])

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(rsync, "sync_all", fake_sync_all)
    monkeypatch.setattr(rsync, "_get_poll_seconds", lambda f: _async_return(600))
    with pytest.raises(asyncio.CancelledError):
        await rsync.repo_sync_loop(_setup_db)
    assert sleeps["n"] == 2


async def test_loop_swallows_generic_error(_setup_db, monkeypatch):
    sleeps = {"n": 0}

    async def fake_sleep(_):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise asyncio.CancelledError()

    async def boom(s):
        raise ValueError("transient")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(rsync, "sync_all", boom)
    monkeypatch.setattr(rsync, "_get_poll_seconds", lambda f: _async_return(600))
    with pytest.raises(asyncio.CancelledError):
        await rsync.repo_sync_loop(_setup_db)


async def test_loop_reraises_cancelled_from_sync(_setup_db, monkeypatch):
    async def ok_sleep(_):
        return None

    async def cancel(s):
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", ok_sleep)
    monkeypatch.setattr(rsync, "sync_all", cancel)
    with pytest.raises(asyncio.CancelledError):
        await rsync.repo_sync_loop(_setup_db)


def _async_return(v):
    async def _coro():
        return v

    return _coro()
