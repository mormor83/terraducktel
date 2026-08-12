"""Regression guards: blocking I/O must not run on the event loop.

The API serves from a single uvicorn worker (`docker-entrypoint.sh` execs
uvicorn with no `--workers`), so a synchronous call made directly from an
`async def` parks *every* in-flight request for its full duration. Two paths
did exactly that:

- `repo_sync._shallow_clone` shells out to `git clone` — `subprocess.run(...,
  timeout=60)` — once per (repo, ref, bu) group. Across a fleet of workspaces a
  sync pass froze the API repeatedly, with worst-case response times pinned to
  that 60s clone timeout.
- `routers/state.get_state` / `put_state` call the deliberately *sync*
  `StateStore` contract (boto3 / azure-blob / gcs) inline, so a cross-account
  GetObject plus a full-body read of a multi-MB tfstate blocked everything.

Each test drives the real code path with only the blocking leaf replaced by a
`time.sleep`, while a ticker coroutine counts how often the loop regained
control. Inline blocking yields ~0 ticks; offloaded work yields many. These
tests fail if either call site is ever moved back onto the loop.
"""
import asyncio
import time

import pytest

from app.auth.internal_token import StateAuth
from app.models.business_unit import DEFAULT_BU_ID
from app.models.workspace import Workspace
from app.routers import state as state_router
from app.services import repo_sync as rsync


pytestmark = pytest.mark.usefixtures("default_bu")

# How long the stand-in blocking call takes.
BLOCK_SECONDS = 0.30
# How often the ticker tries to run.
TICK_SECONDS = 0.01
# Perfectly interleaved we would see ~30 ticks. Assert a low floor so a loaded
# CI box cannot flake this, while inline blocking (0-1 ticks) still fails hard.
MIN_TICKS = 5


class _Ticker:
    """Counts event-loop iterations while running as a background task."""

    def __init__(self) -> None:
        self.ticks = 0
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "_Ticker":
        async def _run() -> None:
            while True:
                await asyncio.sleep(TICK_SECONDS)
                self.ticks += 1

        self._task = asyncio.create_task(_run())
        await asyncio.sleep(0)  # let the task reach its first await
        return self

    async def __aexit__(self, *exc) -> bool:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        return False


def _ws(name: str, tf_dir: str = ".", repo: str = "https://github.com/o/r", ref: str = "main") -> Workspace:
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


async def test_repo_sync_clone_does_not_block_event_loop(db_session, monkeypatch):
    """`check_workspace_paths` must clone off the loop."""

    def blocking_clone(*a, **k):
        time.sleep(BLOCK_SECONDS)  # stands in for subprocess.run(["git", "clone", ...])
        return ("/tmp/clone", None)

    monkeypatch.setattr(rsync, "_shallow_clone", blocking_clone)
    monkeypatch.setattr(rsync, "_cleanup", lambda d: None)

    async with _Ticker() as ticker:
        res = await rsync.check_workspace_paths(db_session, [_ws("a")])

    assert res.checked == 1 and res.ok == 1
    assert ticker.ticks >= MIN_TICKS, (
        f"event loop was blocked during git clone (ticks={ticker.ticks}) — "
        "_shallow_clone must be awaited via asyncio.to_thread"
    )


async def test_repo_sync_cleanup_does_not_block_event_loop(db_session, monkeypatch):
    """rmtree of a cloned worktree must also stay off the loop."""
    monkeypatch.setattr(rsync, "_shallow_clone", lambda *a, **k: ("/tmp/clone", None))

    def blocking_cleanup(tmpdir):
        time.sleep(BLOCK_SECONDS)  # stands in for shutil.rmtree over a worktree

    monkeypatch.setattr(rsync, "_cleanup", blocking_cleanup)

    async with _Ticker() as ticker:
        await rsync.check_workspace_paths(db_session, [_ws("a")])

    assert ticker.ticks >= MIN_TICKS, (
        f"event loop was blocked during cleanup (ticks={ticker.ticks}) — "
        "_cleanup must be awaited via asyncio.to_thread"
    )


async def test_state_get_does_not_block_event_loop(db_session, monkeypatch):
    """GET /api/v1/state/{ws} must read from the store off the loop."""
    ws = _ws("slow-store")
    db_session.add(ws)
    await db_session.commit()

    class _SlowStore:
        def get_state_at(self, key: str) -> bytes:
            time.sleep(BLOCK_SECONDS)  # stands in for GetObject + Body.read()
            return b'{"version": 4}'

    async def fake_service_for(workspace, db):
        return _SlowStore(), "leaf/terraform.tfstate"

    monkeypatch.setattr(state_router, "_service_for", fake_service_for)

    async with _Ticker() as ticker:
        resp = await state_router.get_state(ws.id, StateAuth(workspace_id=None), db_session)

    assert resp.status_code == 200
    assert ticker.ticks >= MIN_TICKS, (
        f"event loop was blocked during state read (ticks={ticker.ticks}) — "
        "get_state_at must be awaited via asyncio.to_thread"
    )


async def test_state_put_does_not_block_event_loop(db_session, monkeypatch):
    """POST /api/v1/state/{ws} must write to the store off the loop."""
    ws = _ws("slow-store-put")
    db_session.add(ws)
    await db_session.commit()

    written: dict = {}

    class _SlowStore:
        def put_state_at(self, key: str, state_bytes: bytes) -> None:
            time.sleep(BLOCK_SECONDS)  # stands in for PutObject
            written["key"] = key

    async def fake_service_for(workspace, db):
        return _SlowStore(), "leaf/terraform.tfstate"

    monkeypatch.setattr(state_router, "_service_for", fake_service_for)

    body = b'{"version": 4, "resources": []}'

    class _Req:
        headers: dict = {}  # no content-length → skips the declared-size check

        async def body(self) -> bytes:
            return body

    async with _Ticker() as ticker:
        resp = await state_router.put_state(
            ws.id, _Req(), StateAuth(workspace_id=None), db_session
        )

    assert resp.status_code == 200 and written["key"] == "leaf/terraform.tfstate"
    assert ticker.ticks >= MIN_TICKS, (
        f"event loop was blocked during state write (ticks={ticker.ticks}) — "
        "put_state_at must be awaited via asyncio.to_thread"
    )
