"""RunStep helpers: seed steps when a run is created, transition individual steps."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run_step import (
    APPLY_EXTRA_STEP_NAMES,
    DEFAULT_STEP_NAMES,
    HELM_APPLY_EXTRA_STEP_NAMES,
    HELM_STEP_NAMES,
    RunStep,
    StepStatus,
)


def step_names_for_command(command: str, kind: str = "terraform") -> list[str]:
    """Return the canonical step list for `plan`, `apply`, `destroy`.

    `kind` selects the terraform (default) or helm timeline.
    """
    if kind == "helm":
        base = list(HELM_STEP_NAMES)
        if command in ("apply", "destroy"):
            base.extend(HELM_APPLY_EXTRA_STEP_NAMES)
        return base
    base = list(DEFAULT_STEP_NAMES)
    if command in ("apply", "destroy"):
        base.extend(APPLY_EXTRA_STEP_NAMES)
    return base


async def seed_steps(
    session: AsyncSession, run_id: str, command: str, kind: str = "terraform"
) -> list[RunStep]:
    """Insert the canonical pending step rows for a freshly-created run."""
    rows: list[RunStep] = []
    for position, name in enumerate(step_names_for_command(command, kind)):
        rows.append(
            RunStep(
                id=str(uuid.uuid4()),
                run_id=run_id,
                position=position,
                name=name,
                status=StepStatus.PENDING,
            )
        )
    session.add_all(rows)
    await session.flush()
    return rows


async def list_steps(session: AsyncSession, run_id: str) -> list[RunStep]:
    result = await session.execute(
        select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.position)
    )
    return list(result.scalars().all())


async def update_step(
    session: AsyncSession,
    step: RunStep,
    *,
    status: Optional[str] = None,
    output: Optional[str] = None,
    summary_json: Optional[str] = None,
) -> RunStep:
    """Apply an in-place update to a step.

    `status` transitions also drive `started_at` / `completed_at` /
    `duration_seconds` so the UI can render durations without extra plumbing.
    """
    now = datetime.now(timezone.utc)
    if status is not None:
        new_status = StepStatus(status)
        if new_status == StepStatus.RUNNING and step.started_at is None:
            step.started_at = now
        if new_status in (StepStatus.SUCCESS, StepStatus.FAILED, StepStatus.SKIPPED):
            step.completed_at = now
            if step.started_at is not None:
                # SQLite returns tz-naive; Postgres returns tz-aware. Normalize so
                # the subtraction works on both backends.
                started = step.started_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                delta = (now - started).total_seconds()
                step.duration_seconds = int(max(0, round(delta)))
            elif step.duration_seconds is None:
                step.duration_seconds = 0
        step.status = new_status
    if output is not None:
        step.output = output
    if summary_json is not None:
        step.summary_json = summary_json
    await session.flush()
    return step
