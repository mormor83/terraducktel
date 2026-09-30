"""Promotions — one push of selected differences from a link's source to its target.

A `Promotion` commits the selected hunks (and "create in target" actions) to
each affected target stack's pinned branch — one commit per (repo, branch) —
and then creates one ordinary run per affected target stack. The pipeline
(plan → Checkov → OPA → cost → approval → apply) is untouched: promotion runs
are plain `apply` runs tagged with `runs.promotion_id`, never auto-approved.

`status` holds the latest known status. The git phase (pending → committing →
commit_failed | running) is set directly; everything after is *derived* from
the runs (`promotion_service.derive_status`) and written back — with an audit
row — whenever a read or a run hook notices it changed. So no run-terminal
path (reject, cancel, reaper, executor failure) needs its own promotion hook.

`PromotionRun` joins a promotion to each target stack it touched; `residual`
holds the post-apply recompare ("verify") and `verified_at` when it ran.
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Git-phase statuses (set directly); the rest are derived from the runs.
GIT_STATUSES = ("pending", "committing", "commit_failed")


class Promotion(Base):
    __tablename__ = "promotions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Human-friendly, BU-independent sequence number for commit messages / UI.
    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    link_id: Mapped[str] = mapped_column(
        String, ForeignKey("env_links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    business_unit_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # "promote" or "revert" (a revert undoes `reverts_promotion_id`'s commits).
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="promote")
    reverts_promotion_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # forward = link source → target; reverse = flipped for this promotion only.
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="forward")
    initiated_by: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {"pairs": [{"pair_id", "hunk_ids": [...], "create_in_target": bool}],
    #  "protected_overrides": [{"hunk_id", "reason"}]}
    selection: Mapped[dict] = mapped_column(JSON, nullable=False)
    selection_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    commit_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"repo_url", "branch", "base_sha", "sha", "files": [...], "stack_ids": [...]}]
    commits: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set explicitly by promotion_service on every status change (no server-side
    # onupdate: an expired server-generated column would lazy-load in async).
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromotionRun(Base):
    __tablename__ = "promotion_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    promotion_id: Mapped[str] = mapped_column(
        String, ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pair_id: Mapped[str | None] = mapped_column(String, nullable=True)
    target_stack_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # True when this promotion created the target stack (create in target).
    created_stack: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Post-apply recompare: {"in_sync": bool, "keys_changed": n, "hunks": [...short]}.
    residual: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verified_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
