"""Queued executor jobs — decouples the run trigger from the executor launch.

A row in `run_jobs` is the worker's todo list: each row points to one `Run`,
and the worker `SELECT … FOR UPDATE SKIP LOCKED`s it, launches the executor,
and updates its state. Heartbeats land here so a single reaper can find dead
runs and release their advisory locks.

States:
  queued       → worker hasn't picked it up yet
  picked       → worker is running terraform (or trying to)
  done         → executor finished and Run.status was set to a terminal value
  failed       → executor crashed or the reaper marked it stale
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RunJobState(str, enum.Enum):
    QUEUED = "queued"
    PICKED = "picked"
    DONE = "done"
    FAILED = "failed"


class RunJob(Base):
    __tablename__ = "run_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    state: Mapped[RunJobState] = mapped_column(
        SAEnum(RunJobState, values_callable=lambda e: [v.value for v in e]),
        nullable=False,
        default=RunJobState.QUEUED,
    )
    # The apply-phase relaunch (post-approval) creates a fresh queued job tied to
    # the same run. `phase` lets the worker pass the right TF_PHASE to the
    # executor without re-reading run state.
    phase: Mapped[str] = mapped_column(String(20), nullable=False, default="plan")
    picked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picked_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
