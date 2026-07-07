"""Per-step lifecycle rows for a Run.

Each Run has a deterministic list of steps (Git Clone → Terraform Plan → Cost
Estimation). The executor updates each row's status + duration as it works,
giving the UI a timeline view of progress.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# Default lifecycle for a `plan` or `apply` run. Order matters — `position`
# tracks display order. `Cost Estimation` is informational and skipped when no
# Infracost token is configured.
DEFAULT_STEP_NAMES: list[str] = [
    "Git Clone",
    "Get Working Directory",
    "Loading terraducktel YAML file",
    "Load Variables",
    "Setting Version",
    "Initialize",
    "Terraform Init",
    "Setting Terraform Workspace",
    "Tag Resources",
    "Checkov Security Scan",
    "Terraform Plan",
    # OPA/conftest gate. Runs AFTER plan because it evaluates the plan JSON
    # (`terraform show -json tfplan`), unlike Checkov which scans source HCL.
    # Skipped when the per-BU opa.mode is `off` (the default).
    "OPA Policy Check",
    "Cost Estimation",
]

# Apply lifecycle adds these AFTER the plan/cost steps. The flow pauses at
# `Awaiting Approval` until a different user clicks Approve in the UI; once
# approved, a second executor pass runs Terraform Apply and the post-apply
# steps.
APPLY_EXTRA_STEP_NAMES: list[str] = [
    "Awaiting Approval",
    "Terraform Apply",
    "After: Terraform Apply",
    "Terraform Output",
    "Store Working Directory",
]

# Helm lifecycle (workspace.kind == "helm"). Mirrors the terraform timeline but
# maps to helm verbs: `Helm Diff` is the plan, `Helm Upgrade` is the apply.
# Cost Estimation is kept as a row (the executor marks it `skipped` for helm) so
# the timeline shape stays consistent across kinds.
HELM_STEP_NAMES: list[str] = [
    "Git Clone",
    "Get Chart Dir",
    "Helm Dependency Build",
    "Lint",
    "Helm Diff",
    "Cost Estimation",
]

# Helm apply/destroy adds these AFTER the plan steps. Pauses at
# `Awaiting Approval`; once approved a second executor pass runs Helm Upgrade.
HELM_APPLY_EXTRA_STEP_NAMES: list[str] = [
    "Awaiting Approval",
    "Helm Upgrade",
    "Helm Output",
]


class RunStep(Base):
    __tablename__ = "run_steps"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # native_enum=False matches migration 007 which created a VARCHAR + CHECK
    # rather than a native Postgres enum type. Without this flag SQLAlchemy
    # tries to CAST values to a `stepstatus` enum that doesn't exist in PG.
    status: Mapped[StepStatus] = mapped_column(
        SAEnum(
            StepStatus,
            name="stepstatus",
            native_enum=False,
            values_callable=lambda enum: [e.value for e in enum],
        ),
        nullable=False,
        default=StepStatus.PENDING,
    )
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-as-text for plan diff (+/~/-) and cost numbers; small enough to
    # piggyback on Text without a dedicated JSON column on SQLite.
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
