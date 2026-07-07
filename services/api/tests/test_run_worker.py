"""Regression tests for the executor job queue (task #9).

`_claim_one` uses Postgres-only SQL (`SELECT … FOR UPDATE SKIP LOCKED`) and is
covered by the live smoke check. These tests exercise the rest of the worker
surface against the in-memory SQLite test DB:

  - enqueue_job creates a RunJob in `queued`
  - heartbeat() bumps heartbeat_at on the currently-picked job
  - _mark_run_and_job_failed flips both Run.status and the job's state,
    increments the executor-failure counter
  - _reap_stale finds picked jobs whose heartbeat is older than the cutoff,
    fails them, and tries to release the workspace lock
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.business_unit import BusinessUnit
from app.models.run import Run, RunStatus
from app.models.run_job import RunJob, RunJobState
from app.models.workspace import Workspace
from app.services import run_worker
from app import observability as obs


async def _make_run(session, *, status=RunStatus.PENDING, branch="main") -> Run:
    bu = BusinessUnit(id=str(uuid.uuid4()), slug=f"bu-{uuid.uuid4().hex[:8]}", name="Test BU")
    session.add(bu)
    await session.flush()
    ws = Workspace(
        id=str(uuid.uuid4()),
        business_unit_id=bu.id,
        name="ws",
        aws_account_id="123456789012",
        environment="dev",
        region="us-east-1",
        repo_ref=branch,
    )
    session.add(ws)
    run = Run(
        id=str(uuid.uuid4()),
        workspace_id=ws.id,
        command="plan",
        status=status,
        branch=branch,
    )
    session.add(run)
    await session.flush()
    return run


# ─── enqueue ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_job_creates_queued_row(db_session):
    run = await _make_run(db_session)
    job = await run_worker.enqueue_job(db_session, run_id=run.id, phase="plan")
    assert job.state == RunJobState.QUEUED
    assert job.run_id == run.id
    assert job.phase == "plan"
    assert job.attempt == 0


@pytest.mark.asyncio
async def test_enqueue_job_records_phase(db_session):
    run = await _make_run(db_session)
    job = await run_worker.enqueue_job(db_session, run_id=run.id, phase="apply")
    assert job.phase == "apply"


# ─── heartbeat ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_updates_picked_job(db_session):
    run = await _make_run(db_session)
    job = RunJob(
        run_id=run.id,
        state=RunJobState.PICKED,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db_session.add(job)
    await db_session.commit()

    ok = await run_worker.heartbeat(db_session, run.id)
    assert ok is True
    await db_session.refresh(job)
    # heartbeat_at must have moved forward by ~5 minutes. SQLite drops tzinfo
    # on round-trip, so normalise both sides to UTC-aware before subtraction.
    hb = job.heartbeat_at
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - hb).total_seconds()
    assert age < 5, f"heartbeat_at not bumped (age={age}s)"


@pytest.mark.asyncio
async def test_heartbeat_returns_false_when_no_picked_job(db_session):
    run = await _make_run(db_session)
    # No job at all
    ok = await run_worker.heartbeat(db_session, run.id)
    assert ok is False


@pytest.mark.asyncio
async def test_heartbeat_bumps_done_job(db_session):
    """The worker flips a job to DONE the moment Docker accepts the executor
    container, but the executor itself keeps running terraform and sending
    heartbeats. heartbeat() must accept DONE jobs so the reaper doesn't fail
    a healthy long-running executor at the 90s mark."""
    run = await _make_run(db_session)
    job = RunJob(
        run_id=run.id,
        state=RunJobState.DONE,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db_session.add(job)
    await db_session.commit()
    ok = await run_worker.heartbeat(db_session, run.id)
    assert ok is True
    await db_session.refresh(job)
    hb = job.heartbeat_at
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - hb).total_seconds()
    assert age < 5, f"heartbeat_at not bumped (age={age}s)"


@pytest.mark.asyncio
async def test_heartbeat_picks_done_job_over_old_picked_job(db_session):
    """If both a PICKED retry attempt and the current DONE job exist for the
    same run, heartbeat() should bump the most recent one (DONE)."""
    run = await _make_run(db_session)
    now = datetime.now(timezone.utc)
    old_picked = RunJob(
        run_id=run.id,
        state=RunJobState.PICKED,
        created_at=now - timedelta(hours=2),
        heartbeat_at=now - timedelta(hours=1),
    )
    fresh_done = RunJob(
        run_id=run.id,
        state=RunJobState.DONE,
        created_at=now - timedelta(minutes=10),
        heartbeat_at=now - timedelta(minutes=5),
    )
    db_session.add(old_picked)
    db_session.add(fresh_done)
    await db_session.commit()

    ok = await run_worker.heartbeat(db_session, run.id)
    assert ok is True
    await db_session.refresh(fresh_done)
    await db_session.refresh(old_picked)
    fresh_hb = fresh_done.heartbeat_at
    if fresh_hb.tzinfo is None:
        fresh_hb = fresh_hb.replace(tzinfo=timezone.utc)
    assert (datetime.now(timezone.utc) - fresh_hb).total_seconds() < 5


# ─── _mark_run_and_job_failed ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_run_and_job_failed_increments_counter(db_session):
    obs._COUNTERS.clear()  # isolate from other tests
    run = await _make_run(db_session)
    job = RunJob(run_id=run.id, state=RunJobState.PICKED)
    db_session.add(job)
    await db_session.flush()

    await run_worker._mark_run_and_job_failed(db_session, run, job.id, "boom")

    await db_session.refresh(run)
    await db_session.refresh(job)
    assert run.status == RunStatus.FAILED
    assert run.error_output == "boom"
    assert job.state == RunJobState.FAILED
    assert job.last_error == "boom"

    text = obs.render_prom_text()
    assert "tdt_executor_failures_total 1.0" in text


@pytest.mark.asyncio
async def test_mark_run_and_job_failed_truncates_long_errors(db_session):
    run = await _make_run(db_session)
    job = RunJob(run_id=run.id, state=RunJobState.PICKED)
    db_session.add(job)
    await db_session.flush()

    huge = "x" * 10_000
    await run_worker._mark_run_and_job_failed(db_session, run, job.id, huge)
    await db_session.refresh(run)
    await db_session.refresh(job)
    # 4000 cap on run.error_output, 2000 cap on job.last_error — protects the
    # Prom text and the UI from massive payloads.
    assert len(run.error_output) <= 4000
    assert len(job.last_error) <= 2000


# ─── _reap_stale ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reap_stale_fails_old_picked_jobs(db_session):
    """A picked job whose heartbeat is older than STALE_AFTER_SECONDS is reaped."""
    # Build a session_factory that yields the same db_session-style session
    # so the worker code finds its rows. Easiest: monkeypatch the factory to
    # return an existing session as a context manager.
    factory = _SessionFactoryFromSession(db_session)

    run = await _make_run(db_session, status=RunStatus.RUNNING)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=run_worker.STALE_AFTER_SECONDS + 10)
    job = RunJob(run_id=run.id, state=RunJobState.PICKED, heartbeat_at=cutoff)
    db_session.add(job)
    await db_session.commit()

    reaped = await run_worker._reap_stale(factory)
    assert reaped == 1

    await db_session.refresh(run)
    await db_session.refresh(job)
    assert run.status == RunStatus.FAILED
    assert "no heartbeat" in (run.error_output or "")
    assert job.state == RunJobState.FAILED


@pytest.mark.asyncio
async def test_reap_stale_leaves_fresh_jobs_alone(db_session):
    factory = _SessionFactoryFromSession(db_session)

    run = await _make_run(db_session, status=RunStatus.RUNNING)
    job = RunJob(
        run_id=run.id,
        state=RunJobState.PICKED,
        heartbeat_at=datetime.now(timezone.utc),  # fresh
    )
    db_session.add(job)
    await db_session.commit()

    reaped = await run_worker._reap_stale(factory)
    assert reaped == 0

    await db_session.refresh(run)
    await db_session.refresh(job)
    assert run.status == RunStatus.RUNNING
    assert job.state == RunJobState.PICKED


@pytest.mark.asyncio
async def test_reap_stale_reaps_done_job_with_non_terminal_run(db_session):
    """A DONE job whose Run is still running means the worker handed off to
    the executor but the executor died without ever sending a heartbeat
    (e.g. the entrypoint exited on a missing AWS_ACCESS_KEY_ID check). The
    reaper must catch this case so the run doesn't sit in `running` forever.
    """
    factory = _SessionFactoryFromSession(db_session)

    run = await _make_run(db_session, status=RunStatus.RUNNING)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=run_worker.STALE_AFTER_SECONDS + 10)
    job = RunJob(run_id=run.id, state=RunJobState.DONE, heartbeat_at=cutoff)
    db_session.add(job)
    await db_session.commit()

    reaped = await run_worker._reap_stale(factory)
    assert reaped == 1

    await db_session.refresh(run)
    await db_session.refresh(job)
    assert run.status == RunStatus.FAILED
    assert "executor died" in (run.error_output or "")
    assert job.state == RunJobState.FAILED


@pytest.mark.asyncio
async def test_reap_stale_leaves_completed_plan_only_run_alone(db_session):
    """Regression: a plan-only run finishes at PLANNED — the executor PATCHes
    status=planned and exits, so its job goes DONE and its heartbeat freezes.
    The reaper must NOT flip that completed run to FAILED (it used to, ~90s
    later, with 'executor died before reporting any step status', even though
    every step succeeded). PLANNED is terminal for plan-only runs."""
    factory = _SessionFactoryFromSession(db_session)

    run = await _make_run(db_session, status=RunStatus.PLANNED)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=run_worker.STALE_AFTER_SECONDS + 30)
    job = RunJob(run_id=run.id, state=RunJobState.DONE, heartbeat_at=cutoff)
    db_session.add(job)
    await db_session.commit()

    reaped = await run_worker._reap_stale(factory)
    assert reaped == 0

    await db_session.refresh(run)
    await db_session.refresh(job)
    assert run.status == RunStatus.PLANNED   # untouched — plan completed
    assert job.state == RunJobState.DONE


@pytest.mark.asyncio
async def test_reap_stale_ignores_old_phase_job_when_newer_exists(db_session):
    """Multi-phase regression. A plan-phase job goes DONE when the plan
    executor exits cleanly; its heartbeat_at is then frozen forever. When
    the user later approves and the apply phase begins, the run flips to
    APPLYING (a reapable status). Without a "latest job per run" filter the
    reaper saw the frozen plan-phase heartbeat and force-failed the live
    apply. This test pins the guard: an older job whose run already has a
    newer job in the queue must be left alone."""
    factory = _SessionFactoryFromSession(db_session)

    run = await _make_run(db_session, status=RunStatus.APPLYING)
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=run_worker.STALE_AFTER_SECONDS + 30)
    plan_job = RunJob(
        run_id=run.id,
        phase="plan",
        state=RunJobState.DONE,
        created_at=now - timedelta(minutes=10),
        heartbeat_at=stale,
        picked_by="plan-worker",
    )
    apply_job = RunJob(
        run_id=run.id,
        phase="apply",
        state=RunJobState.DONE,
        created_at=now - timedelta(seconds=5),
        heartbeat_at=now,
        picked_by="apply-worker",
    )
    db_session.add(plan_job)
    db_session.add(apply_job)
    await db_session.commit()

    reaped = await run_worker._reap_stale(factory)
    assert reaped == 0
    await db_session.refresh(run)
    await db_session.refresh(plan_job)
    await db_session.refresh(apply_job)
    assert run.status == RunStatus.APPLYING
    assert plan_job.state == RunJobState.DONE
    assert apply_job.state == RunJobState.DONE
    assert run.error_output is None


@pytest.mark.asyncio
async def test_reap_stale_uses_latest_job_when_all_stale(db_session):
    """If both phases' jobs are stale (e.g. the apply executor truly did
    die), the reaper must reap the run using the latest job — the plan job
    is just history at this point. error_output should name the apply-phase
    worker so on-call knows which executor to investigate."""
    factory = _SessionFactoryFromSession(db_session)

    run = await _make_run(db_session, status=RunStatus.APPLYING)
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=run_worker.STALE_AFTER_SECONDS + 30)
    plan_job = RunJob(
        run_id=run.id,
        phase="plan",
        state=RunJobState.DONE,
        created_at=now - timedelta(minutes=10),
        heartbeat_at=stale,
        picked_by="plan-worker",
    )
    apply_job = RunJob(
        run_id=run.id,
        phase="apply",
        state=RunJobState.DONE,
        created_at=now - timedelta(seconds=120),
        heartbeat_at=stale,
        picked_by="apply-worker",
    )
    db_session.add(plan_job)
    db_session.add(apply_job)
    await db_session.commit()

    reaped = await run_worker._reap_stale(factory)
    assert reaped == 1
    await db_session.refresh(run)
    await db_session.refresh(plan_job)
    await db_session.refresh(apply_job)
    assert run.status == RunStatus.FAILED
    assert plan_job.state == RunJobState.DONE, "older plan job must not be touched"
    assert apply_job.state == RunJobState.FAILED
    assert "apply-worker" in (run.error_output or "")


@pytest.mark.asyncio
async def test_reap_stale_leaves_done_jobs_with_terminal_runs(db_session):
    """A DONE job whose Run is APPLIED / FAILED / CANCELLED is a normal
    finished run — the reaper must not touch it."""
    factory = _SessionFactoryFromSession(db_session)

    run = await _make_run(db_session, status=RunStatus.APPLIED)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=run_worker.STALE_AFTER_SECONDS + 10)
    job = RunJob(run_id=run.id, state=RunJobState.DONE, heartbeat_at=cutoff)
    db_session.add(job)
    await db_session.commit()

    reaped = await run_worker._reap_stale(factory)
    assert reaped == 0

    await db_session.refresh(run)
    await db_session.refresh(job)
    assert run.status == RunStatus.APPLIED  # untouched
    assert job.state == RunJobState.DONE


@pytest.mark.asyncio
async def test_heartbeat_keeps_healthy_long_run_alive_through_reaper(db_session):
    """End-to-end of the false-failure bug: a job is DONE (container launched),
    the run is still PLANNING / APPLYING, and the executor is sending
    heartbeats every 30s. With the fix in place those heartbeats bump
    heartbeat_at, and a reaper sweep finds nothing to do."""
    factory = _SessionFactoryFromSession(db_session)

    run = await _make_run(db_session, status=RunStatus.APPLYING)
    job = RunJob(
        run_id=run.id,
        state=RunJobState.DONE,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=run_worker.STALE_AFTER_SECONDS + 30),
    )
    db_session.add(job)
    await db_session.commit()

    # Simulate the executor's /heartbeat POST.
    ok = await run_worker.heartbeat(db_session, run.id)
    assert ok is True

    reaped = await run_worker._reap_stale(factory)
    assert reaped == 0
    await db_session.refresh(run)
    assert run.status == RunStatus.APPLYING


@pytest.mark.asyncio
async def test_reap_stale_terminal_run_does_not_crash(db_session):
    """If the run is already terminal, the reaper must skip the transition and
    still mark the job failed. Regression for the bug where ValueError from
    Run.transition() would bubble up and abort the reap pass."""
    factory = _SessionFactoryFromSession(db_session)

    run = await _make_run(db_session, status=RunStatus.APPLIED)  # terminal
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=run_worker.STALE_AFTER_SECONDS + 10)
    job = RunJob(run_id=run.id, state=RunJobState.PICKED, heartbeat_at=cutoff)
    db_session.add(job)
    await db_session.commit()

    reaped = await run_worker._reap_stale(factory)
    assert reaped == 1
    await db_session.refresh(job)
    assert job.state == RunJobState.FAILED


# ─── Helper: present a single session as an async_sessionmaker-compatible ───
# ─── object so the worker's `async with session_factory() as session:` works.


class _SessionFactoryFromSession:
    """Wrap a single AsyncSession in something callable that yields it.

    The real run_worker uses an `async_sessionmaker`, but tests don't need a
    real connection pool — we just need `async with factory() as session:` to
    hand back the SAME session for the whole test so assertions can read what
    the worker wrote without isolation surprises.
    """

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return _CtxSession(self._session)


class _CtxSession:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        # Don't close the session — the test owns its lifetime.
        return False
