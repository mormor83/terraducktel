"""Pydantic schemas for runs."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.variable import RunVariable


RunCommand = Literal["plan", "apply", "destroy"]


class RunCreate(BaseModel):
    command: RunCommand = "plan"
    # Optional per-run variable additions/overrides. Merged onto global +
    # workspace layers at executor launch (last wins per key). Stored
    # encrypted on the run row so the apply phase replays the exact same
    # values the planner produced.
    variables: Optional[list[RunVariable]] = None
    # Optional branch override — if set, the workspace's `repo_ref` is
    # updated atomically with the trigger so the dashboard chip and drift
    # detector follow the chosen branch from this run forward. To change the
    # branch without spawning a run, call PUT /workspaces/{id} directly.
    branch: Optional[str] = None
    # When true, an apply/destroy run whose plan succeeds with all gates
    # green and a 0/0/0 summary is approved by the system (no human in the
    # loop). The audit log carries an explicit "auto-approved" entry so the
    # decision is traceable. Ignored when the command is `plan` (no apply
    # phase to approve).
    auto_approve_if_no_changes: bool = False
    # Only honored when `auto_approve_if_no_changes` is true AND the plan is
    # 0/0/0. When true, skip the apply phase entirely (faster, no executor
    # spawn). When false, still execute a no-op apply so the run timeline
    # looks identical to a normal apply.
    auto_approve_skip_apply: bool = False


class RunUpdate(BaseModel):
    """Executor-driven status transitions (operator+).

    Accepts either `plan_output` (the canonical name) or the legacy `output`
    field that pre-phase-2 executor entrypoints emitted; if both are sent the
    canonical name wins. New executors must send `plan_output`.
    """

    status: Optional[str] = None
    plan_output: Optional[str] = None
    # Structured `terraform show -json tfplan`, used by the Approvals canvas.
    plan_json: Optional[str] = None
    # Base64-encoded raw `tfplan` binary; saved after plan, restored on apply.
    tfplan_b64: Optional[str] = None
    # OPA gate outcome, stamped by the executor's OPA Policy Check step.
    policy_status: Optional[str] = None
    output: Optional[str] = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _coalesce_plan_output(self) -> "RunUpdate":
        if self.plan_output is None and self.output is not None:
            object.__setattr__(self, "plan_output", self.output)
        return self


class ApprovalBody(BaseModel):
    comment: Optional[str] = None


class RunResponse(BaseModel):
    id: str
    workspace_id: str
    triggered_by: Optional[str] = None
    reviewer_id: Optional[str] = None
    command: str
    status: str
    branch: Optional[str] = None
    plan_output: Optional[str] = None
    error_output: Optional[str] = None
    # OPA/conftest gate outcome: not_run | passed | warned | failed.
    policy_status: str = "not_run"
    # Phase-13: created/started/completed timestamps so the Runs page can show
    # context ("5m ago", duration) instead of bare run IDs. FastAPI serializes
    # datetimes as ISO-8601 strings — the UI parses them with `new Date()`.
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # Per-run auto-approve flags, echoed back so clients can confirm what
    # the worker will do.
    auto_approve_if_no_changes: bool = False
    auto_approve_skip_apply: bool = False

    model_config = {"from_attributes": True}
