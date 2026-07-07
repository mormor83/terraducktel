"""Background worker for the executor job queue.

One asyncio task per API process: polls `run_jobs`, claims queued rows via
SELECT … FOR UPDATE SKIP LOCKED, calls executor_service.launch_run, and marks
the row done/failed. A second pass reaps `picked` rows whose heartbeat is stale
(no PATCH from the executor in `STALE_AFTER_SECONDS`) — these get marked
failed, the Run goes to FAILED, and the advisory state-lock is released so the
next run on the workspace isn't blocked forever.

Concurrency: pool_size copies of this loop can run safely thanks to SKIP
LOCKED. For now we run one worker per API container; horizontal scale is
"add more API replicas" with no extra wiring.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.jwt import create_run_token
from app.models.run import Run, RunStatus
from app.models.run_job import RunJob, RunJobState
from app.models.user import User
from app.models.workspace import Workspace
from app.observability import counter_inc, gauge_set, histogram_observe

logger = logging.getLogger(__name__)


# Defaults — kept as module-level constants so tests and code without a DB
# session can reference them, but the live loops below read from
# `runtime_settings` (config table, 60s TTL cache) on each iteration so
# operators can re-tune without a restart.
POLL_INTERVAL_SECONDS = 2.0
STALE_AFTER_SECONDS = 90
REAPER_INTERVAL_SECONDS = 30.0


async def _get_float_setting(session, key: str, default: float) -> float:
    """Read one float setting; falls back to `default` on any error so a
    misconfigured config table can never wedge the worker loop."""
    try:
        from app.services import runtime_settings

        return float(await runtime_settings.get_value(session, key))
    except Exception:
        return default


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


# ──────────────────────────────────────────────────────────────────────────
# Public API: enqueue
# ──────────────────────────────────────────────────────────────────────────


async def enqueue_job(db: AsyncSession, *, run_id: str, phase: str = "plan") -> RunJob:
    """Create a queued job. Returns the row; caller commits."""
    job = RunJob(run_id=run_id, phase=phase)
    db.add(job)
    await db.flush()
    return job


# ──────────────────────────────────────────────────────────────────────────
# Worker loop
# ──────────────────────────────────────────────────────────────────────────


async def _claim_one(session: AsyncSession) -> RunJob | None:
    """Claim the oldest queued job atomically. Returns None if none available.

    Uses SKIP LOCKED so multiple workers (in same or different API replicas)
    never grab the same job. The session must own the transaction — we COMMIT
    inside this function to release the row-lock as soon as the row is marked
    `picked`.
    """
    result = await session.execute(
        text(
            """
            SELECT id FROM run_jobs
            WHERE state = 'queued'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
    )
    row = result.first()
    if row is None:
        return None
    job_id = row[0]

    job = await session.get(RunJob, job_id)
    if job is None:
        return None
    job.state = RunJobState.PICKED
    job.picked_by = _worker_id()
    job.picked_at = _now()
    job.heartbeat_at = _now()
    job.attempt += 1
    await session.commit()
    return job


async def _run_one(session_factory: async_sessionmaker, job: RunJob) -> None:
    """Actually launch the executor for a claimed job."""
    async with session_factory() as session:
        run = await session.get(Run, job.run_id)
        if run is None:
            await _mark_job_failed(session, job.id, "Run row disappeared between enqueue and pick")
            return

        ws = await session.get(Workspace, run.workspace_id)
        if ws is None:
            await _mark_run_and_job_failed(
                session, run, job.id, "Workspace was deleted before the executor could run."
            )
            return

        # Mint a RUN-SCOPED service token for the executor. It carries
        # the triggering user's identity for audit (`sub`), but no role/superadmin
        # claim: get_current_user confines it to this run's callback routes,
        # current_bu pins it to the run's BU, and require_role caps it at
        # operator — so a superadmin-triggered run can't be used to drive the
        # rest of the API from inside the (semi-trusted) executor container.
        # The admin fallback now only supplies an audit identity, not privilege.
        actor: User | None = None
        if run.triggered_by:
            actor = await session.get(User, run.triggered_by)
        if actor is None:
            # Fall back to any admin — keeps runs alive when users get deleted.
            r = await session.execute(select(User).where(User.role == "admin").limit(1))
            actor = r.scalars().first()
        if actor is None:
            await _mark_run_and_job_failed(
                session, run, job.id, "No actor available to mint executor token (no admin in DB)."
            )
            return

        api_token = create_run_token(
            actor.id,
            actor.email,
            run_id=run.id,
            workspace_id=ws.id,
            business_unit_id=getattr(ws, "business_unit_id", None),
        )

        # Use the same factory the router used: respects EXECUTOR_ENABLED,
        # wires the docker client + ConfigService with the encryption key.
        from app.routers.runs import _get_executor_service

        executor = _get_executor_service(session)
        if executor is None:
            await _mark_run_and_job_failed(
                session,
                run,
                job.id,
                "Executor is not enabled (set EXECUTOR_ENABLED=true and ensure Docker is reachable).",
            )
            return
        try:
            await executor.launch_run(run, ws, api_token=api_token, db_session=session, phase=job.phase)
        except Exception as exc:
            logger.exception("Executor launch failed for run %s", run.id)
            await _mark_run_and_job_failed(session, run, job.id, f"Executor launch failed: {exc!r}")
            return

        # If we got here, the executor container is running. The actual
        # plan/apply outcome is reported via /api/v1/runs/{id} PATCH, which
        # transitions the Run FSM. The job is "done" from the worker's POV.
        j = await session.get(RunJob, job.id)
        if j is not None:
            j.state = RunJobState.DONE
            j.heartbeat_at = _now()
            # Observed time-to-launch — from the moment the job was enqueued
            # to the moment Docker accepted the container. Doesn't capture the
            # terraform runtime; the executor reports that separately via the
            # run-step timeline.
            if j.created_at is not None:
                delta = (_now() - j.created_at).total_seconds()
                histogram_observe(
                    "tdt_run_launch_latency_seconds", delta, {"phase": job.phase}
                )
        await session.commit()


async def _mark_job_failed(session: AsyncSession, job_id: str, reason: str) -> None:
    j = await session.get(RunJob, job_id)
    if j is None:
        return
    j.state = RunJobState.FAILED
    j.last_error = reason[:2000]
    await session.commit()


async def _mark_run_and_job_failed(
    session: AsyncSession, run: Run, job_id: str, reason: str
) -> None:
    try:
        run.transition(RunStatus.FAILED)
    except ValueError:
        # Already terminal — ignore.
        pass
    run.error_output = reason[:4000]
    counter_inc("tdt_executor_failures_total")
    await _mark_job_failed(session, job_id, reason)


# ──────────────────────────────────────────────────────────────────────────
# Reaper — releases stale advisory locks + fails dead runs
# ──────────────────────────────────────────────────────────────────────────


# Run statuses that still expect heartbeats from a running executor. Once a
# run has reached a terminal state we leave it alone — the reaper's job is
# to fail wedged runs, not to revisit completed ones.
#
# NOTE: PLANNED is intentionally EXCLUDED. A plan-only run (`command=plan`)
# finishes at PLANNED — the executor PATCHes status=planned and exits, so its
# job is DONE and its heartbeat freezes forever. Including PLANNED here made the
# reaper flip every completed plan-only run to FAILED ~90s later ("executor died
# before reporting any step status") even though every step succeeded. Apply /
# destroy runs never rest at PLANNED — they PATCH straight to AWAITING_APPROVAL
# (see Run._VALID_TRANSITIONS), so dropping PLANNED loses no wedged-run coverage.
_REAPABLE_RUN_STATUSES = (
    RunStatus.RUNNING,
    RunStatus.PLANNING,
    RunStatus.APPLYING,
)


async def _reap_stale(session_factory: async_sessionmaker) -> int:
    """Find jobs whose executor has stopped sending heartbeats.

    Two flavours:
    - PICKED + stale: the worker claimed the job but never finished the
      executor handoff. This was the only case the original reaper
      handled.
    - DONE + stale + Run still in a non-terminal status: the worker
      successfully called RunTask, marked the job DONE, then the
      executor died (e.g. exited at the entrypoint require-vars
      check). No heartbeats ever arrived because no step ever started.
      Without this branch the run sat in `running` forever — see the
      Cloudflare/non-AWS executor incident.
    """
    async with session_factory() as session:
        stale_after = await _get_float_setting(
            session, "worker.stale_after_seconds", STALE_AFTER_SECONDS
        )
    cutoff = _now() - timedelta(seconds=stale_after)
    async with session_factory() as session:
        result = await session.execute(
            select(RunJob)
            .where(
                or_(
                    RunJob.state == RunJobState.PICKED,
                    RunJob.state == RunJobState.DONE,
                )
            )
            .where(RunJob.heartbeat_at < cutoff)
        )
        candidates = list(result.scalars().all())
        reaped = 0
        for job in candidates:
            # Only the latest job per run is "active". Multi-phase runs (plan
            # → awaiting_approval → apply) leave behind a DONE plan-phase job
            # whose heartbeat is permanently frozen the moment the plan-phase
            # executor exits cleanly. When the user approves and the apply
            # phase begins, the run transitions to APPLYING (a reapable
            # status) — without this guard the reaper would see that ancient
            # plan-phase job, treat its frozen heartbeat as "executor died",
            # and fail a perfectly healthy in-flight apply. Skip any job that
            # has been superseded by a newer enqueue for the same run.
            newer_exists = (
                await session.execute(
                    select(RunJob.id)
                    .where(RunJob.run_id == job.run_id)
                    .where(RunJob.created_at > job.created_at)
                    .limit(1)
                )
            ).scalar()
            if newer_exists is not None:
                continue

            run = await session.get(Run, job.run_id)
            # DONE jobs are only reapable when the Run is still expecting
            # work from an executor — a successful run that reached
            # APPLIED / FAILED / CANCELLED has nothing for us to fix.
            if job.state == RunJobState.DONE:
                if run is None or run.status not in _REAPABLE_RUN_STATUSES:
                    continue

            if run is not None:
                try:
                    run.transition(RunStatus.FAILED)
                except ValueError:
                    pass
                reason_kind = (
                    "no heartbeats from worker"
                    if job.state == RunJobState.PICKED
                    else "executor died before reporting any step status"
                )
                run.error_output = (
                    f"Run reaped after {int(stale_after)}s — {reason_kind} "
                    f"({job.picked_by!r})."
                )
                # Best-effort: release the advisory lock on the workspace's
                # state. The advisory-lock key is the workspace_id hashed to
                # a bigint inside state_service; we delegate to that module so
                # the hashing stays single-sourced.
                try:
                    from app.services import state_service

                    if hasattr(state_service, "release_workspace_lock"):
                        await state_service.release_workspace_lock(session, run.workspace_id)
                except Exception:
                    logger.exception("Lock release failed during reap of run %s", run.id)
            job.state = RunJobState.FAILED
            job.last_error = "reaped by background reaper (no heartbeat)"
            reaped += 1
        if reaped:
            await session.commit()
        return reaped


# ──────────────────────────────────────────────────────────────────────────
# Lifecycle: started by FastAPI lifespan
# ──────────────────────────────────────────────────────────────────────────


async def worker_loop(session_factory: async_sessionmaker) -> None:
    """Main poll-and-launch loop. One coroutine per API process."""
    logger.info("run_worker: starting (worker_id=%s)", _worker_id())
    while True:
        try:
            async with session_factory() as session:
                job = await _claim_one(session)
                poll = await _get_float_setting(
                    session, "worker.poll_interval_seconds", POLL_INTERVAL_SECONDS
                )
            if job is None:
                await asyncio.sleep(poll)
                continue
            await _run_one(session_factory, job)
        except asyncio.CancelledError:
            logger.info("run_worker: cancelled; shutting down")
            raise
        except Exception:
            logger.exception("run_worker: unexpected error in loop")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def gauges_loop(session_factory: async_sessionmaker) -> None:
    """Refresh business-metric gauges every 15s.

    Cheap aggregate queries; touches three small tables. Splits scope between
    the worker loop (event-driven counters) and this loop (point-in-time gauges)
    so /metrics scrapes are O(1).
    """
    from datetime import datetime, timezone

    while True:
        try:
            async with session_factory() as session:
                # Queue depth: number of queued jobs older than the worker's
                # poll interval. Sustained > 0 = workers can't keep up.
                qd = await session.execute(
                    text("SELECT count(*) FROM run_jobs WHERE state = 'queued'")
                )
                gauge_set("tdt_run_queue_depth_gauge", float(qd.scalar() or 0))

                # Oldest awaiting_approval (seconds). High value = approval is
                # stuck; combine with reviewer_id to know who's holding it.
                pending = await session.execute(
                    text(
                        """
                        SELECT EXTRACT(EPOCH FROM (now() - min(created_at)))
                        FROM runs WHERE status = 'awaiting_approval'
                        """
                    )
                )
                gauge_set("tdt_approval_pending_seconds_gauge", float(pending.scalar() or 0))

                # Oldest drift report (seconds). Catches a stuck drift-detector.
                # NB: drift_reports uses `detected_at`, not the standard
                # `created_at` other tables use — see app/models/drift_report.py.
                drift = await session.execute(
                    text(
                        """
                        SELECT EXTRACT(EPOCH FROM (now() - min(detected_at)))
                        FROM drift_reports
                        """
                    )
                )
                gauge_set("tdt_drift_age_seconds_gauge", float(drift.scalar() or 0))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("run_worker: gauges loop error")
        await asyncio.sleep(15.0)


async def reaper_loop(session_factory: async_sessionmaker) -> None:
    """Stale-job reaper. Interval is read from `worker.reaper_interval_seconds`."""
    logger.info("run_worker: reaper starting")
    while True:
        try:
            n = await _reap_stale(session_factory)
            if n:
                logger.warning("run_worker: reaped %d stale job(s)", n)
            async with session_factory() as session:
                interval = await _get_float_setting(
                    session, "worker.reaper_interval_seconds", REAPER_INTERVAL_SECONDS
                )
        except asyncio.CancelledError:
            logger.info("run_worker: reaper cancelled")
            raise
        except Exception:
            logger.exception("run_worker: reaper error")
            interval = REAPER_INTERVAL_SECONDS
        await asyncio.sleep(interval)


async def heartbeat(session: AsyncSession, run_id: str) -> bool:
    """Bump heartbeat_at for the most recent non-terminal job of `run_id`.

    Accepts both PICKED and DONE. The worker flips a job to DONE the moment
    Docker accepts the executor container, but the executor itself keeps
    running terraform — and sending heartbeats — for the whole plan/apply.
    If we only updated PICKED jobs, a healthy long-running executor would
    stop refreshing its row, and the reaper (which looks at DONE-with-stale-
    heartbeat for non-terminal runs) would flip the live run to FAILED at
    the 90s mark. See regression test test_heartbeat_bumps_done_job.
    """
    result = await session.execute(
        select(RunJob)
        .where(RunJob.run_id == run_id)
        .where(RunJob.state.in_((RunJobState.PICKED, RunJobState.DONE)))
        .order_by(RunJob.created_at.desc())
        .limit(1)
    )
    job = result.scalars().first()
    if job is None:
        return False
    job.heartbeat_at = _now()
    await session.commit()
    return True
