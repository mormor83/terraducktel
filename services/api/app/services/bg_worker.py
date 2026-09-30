"""In-process worker for `bg_jobs` (environment compares + promotion verifies).

Same shape as the run worker: one asyncio task per API process claims queued
rows with `SELECT … FOR UPDATE SKIP LOCKED` (so replicas never double-process),
runs the handler in its own session, and marks the row done / failed. A sweep
every `SWEEP_SECONDS` re-queues verifies for promotion runs that reached
`applied` without one — the backstop for an API restart between the run
finishing and the verify being enqueued.

Handlers are plain async functions keyed by `kind`; tests call `process_one`
directly (SQLite has no SKIP LOCKED and the lifespan loops don't run there).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.bg_job import BgJob

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0
SWEEP_SECONDS = 60.0
STALE_PICKED = timedelta(minutes=10)
MAX_ATTEMPTS = 3


async def enqueue(db: AsyncSession, kind: str, payload: dict, dedupe_key: Optional[str] = None) -> Optional[BgJob]:
    """Queue a job unless an identical one is already queued. Caller commits."""
    if dedupe_key:
        dup = (
            await db.execute(
                select(BgJob.id).where(BgJob.dedupe_key == dedupe_key, BgJob.state.in_(("queued", "picked")))
            )
        ).first()
        if dup is not None:
            return None
    job = BgJob(kind=kind, payload=payload, dedupe_key=dedupe_key, state="queued")
    db.add(job)
    await db.flush()
    return job


async def is_pending(db: AsyncSession, dedupe_key: str) -> bool:
    return (
        await db.execute(
            select(BgJob.id).where(BgJob.dedupe_key == dedupe_key, BgJob.state.in_(("queued", "picked")))
        )
    ).first() is not None


# ─── handlers ────────────────────────────────────────────────────────────────


async def _h_compare(db: AsyncSession, payload: dict) -> None:
    from app.models.env_link import EnvPair
    from app.services import env_compare_service as cmp

    pair = await db.get(EnvPair, payload["pair_id"])
    if pair is None:
        return
    await cmp.compute(db, pair, payload.get("direction") or "forward", force=bool(payload.get("force")))
    await db.commit()


async def _h_verify(db: AsyncSession, payload: dict) -> None:
    from app.services import promotion_service

    await promotion_service.verify(db, payload["promotion_run_id"])


HANDLERS: dict[str, Callable[[AsyncSession, dict], Awaitable[None]]] = {
    "env_compare": _h_compare,
    "env_verify": _h_verify,
}


# ─── claim / process ─────────────────────────────────────────────────────────


async def _claim(session: AsyncSession) -> Optional[BgJob]:
    if session.bind.dialect.name == "postgresql":
        row = (
            await session.execute(
                text(
                    "SELECT id FROM bg_jobs WHERE state = 'queued' ORDER BY created_at "
                    "FOR UPDATE SKIP LOCKED LIMIT 1"
                )
            )
        ).first()
        job_id = row[0] if row else None
    else:
        job_id = (
            await session.execute(select(BgJob.id).where(BgJob.state == "queued").order_by(BgJob.created_at).limit(1))
        ).scalar()
    if job_id is None:
        return None
    job = await session.get(BgJob, job_id)
    job.state = "picked"
    job.attempts += 1
    job.picked_at = datetime.now(timezone.utc)
    await session.commit()
    return job


async def process_one(session_factory: async_sessionmaker) -> bool:
    """Claim and run one job. Returns False when the queue was empty."""
    async with session_factory() as s:
        job = await _claim(s)
        if job is None:
            return False
        job_id, kind, payload = job.id, job.kind, dict(job.payload or {})
    handler = HANDLERS.get(kind)
    error: Optional[str] = None
    try:
        if handler is None:
            raise RuntimeError(f"no handler for job kind {kind!r}")
        async with session_factory() as s:
            await handler(s, payload)
    except Exception as e:  # noqa: BLE001 — a bad job must not kill the loop
        logger.exception("bg_worker: job %s (%s) failed", job_id, kind)
        error = f"{type(e).__name__}: {e}"[:2000]
    async with session_factory() as s:
        job = await s.get(BgJob, job_id)
        if job is not None:
            if error and job.attempts < MAX_ATTEMPTS:
                job.state = "queued"
                job.error = error
            else:
                job.state = "failed" if error else "done"
                job.error = error
                job.finished_at = datetime.now(timezone.utc)
            await s.commit()
    return True


async def drain(session_factory: async_sessionmaker, limit: int = 1000) -> int:
    """Process queued jobs until empty (tests / one-shot use)."""
    n = 0
    while n < limit and await process_one(session_factory):
        n += 1
    return n


async def sweep(session_factory: async_sessionmaker) -> None:
    """Re-queue verifies for applied promotion runs that never got one, and
    un-stick jobs whose worker vanished mid-flight."""
    from app.models.promotion import PromotionRun
    from app.models.run import Run, RunStatus

    async with session_factory() as s:
        rows = (
            await s.execute(
                select(PromotionRun.id)
                .join(Run, Run.id == PromotionRun.run_id)
                .where(PromotionRun.verified_at.is_(None), Run.status == RunStatus.APPLIED)
            )
        ).all()
        for (pr_id,) in rows:
            await enqueue(s, "env_verify", {"promotion_run_id": pr_id}, dedupe_key=f"verify:{pr_id}")
        cutoff = datetime.now(timezone.utc) - STALE_PICKED
        for job in (await s.execute(select(BgJob).where(BgJob.state == "picked", BgJob.picked_at < cutoff))).scalars():
            job.state = "queued"
        await s.commit()


async def bg_loop(session_factory: async_sessionmaker) -> None:
    logger.info("bg_worker: starting")
    last_sweep = 0.0
    loop = asyncio.get_event_loop()
    while True:
        try:
            if loop.time() - last_sweep > SWEEP_SECONDS:
                await sweep(session_factory)
                last_sweep = loop.time()
            if not await process_one(session_factory):
                await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            logger.info("bg_worker: cancelled; shutting down")
            raise
        except Exception:
            logger.exception("bg_worker: loop error")
            await asyncio.sleep(POLL_SECONDS * 5)
