import uuid
from sqlalchemy import String, Text, DateTime, ForeignKey, Boolean, Integer, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DriftReport(Base):
    __tablename__ = "drift_reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(String, ForeignKey("workspaces.id"), nullable=False)
    has_drift: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-category drift breakdown (see migration 033). The 4 canonical types:
    #   modified  — resource in code + cloud, attributes changed (resource_drift update)
    #   untracked — resource in cloud, absent from state (live-AWS scan vs tfstate)
    #   deleted   — resource in state, gone from cloud (resource_drift delete)
    #   mismatch  — state diverges from .tf config (resource_changes not from drift)
    modified_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    untracked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    deleted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    mismatch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # List of {address, type, provider, drift_type, summary} for drill-down.
    resources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    detected_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
