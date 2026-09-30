"""Generic background jobs (environment compares, promotion verifies).

The run queue (`run_jobs`) is shaped around the executor — a job is a run
phase. Compare and verify work is API-side and short, so it gets its own tiny
queue claimed the same way (`SELECT … FOR UPDATE SKIP LOCKED`) by an
in-process loop (`app/services/bg_worker.py`). No new service.

`dedupe_key` collapses repeat enqueues: while a job with the same key is still
queued, another enqueue is a no-op (a user hammering Recompare, or the verify
sweep re-noticing the same applied run).
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BgJob(Base):
    __tablename__ = "bg_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    # queued | picked | done | failed
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    picked_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
