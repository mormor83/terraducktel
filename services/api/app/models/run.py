import uuid
import enum
import re
from sqlalchemy import Boolean, String, Text, DateTime, ForeignKey, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


_AWS_KEY_RE = re.compile(r'AKIA[A-Z0-9]{16}')
_AWS_SECRET_RE = re.compile(
    r'(?i)(aws.{0,20}secret|secret.{0,20}access).{0,20}[=:\s][A-Za-z0-9/+]{40}'
)


def scrub_credentials(text: str) -> str:
    if not text:
        return text
    text = _AWS_KEY_RE.sub('[REDACTED-AWS-KEY]', text)
    text = _AWS_SECRET_RE.sub('[REDACTED-AWS-SECRET]', text)
    return text


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PLANNING = "planning"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Valid FSM transitions: {from_status: set of allowed next statuses}
# Valid FSM transitions: {from_status: set of allowed next statuses}.
# `FAILED` is allowed from EVERY non-terminal state — the executor needs to be
# able to report a fatal error (e.g. terraform init crashed before the run ever
# reached the planning phase) without being blocked by FSM strictness.
_VALID_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED},
    # `running → planned` is allowed: the executor doesn't always emit an
    # explicit `planning` PATCH (terraform init + plan run as one phase).
    # `running → awaiting_approval` is allowed for the unified plan→apply flow:
    # when an `apply` or `destroy` is triggered, the executor runs plan inline,
    # captures tfplan, and PATCHes straight to AWAITING_APPROVAL — no separate
    # PLANNED hop in between.
    RunStatus.RUNNING: {
        RunStatus.PLANNING,
        RunStatus.PLANNED,
        RunStatus.AWAITING_APPROVAL,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    RunStatus.PLANNING: {RunStatus.PLANNED, RunStatus.AWAITING_APPROVAL, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.PLANNED: {RunStatus.AWAITING_APPROVAL, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.AWAITING_APPROVAL: {RunStatus.APPLYING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.APPLYING: {RunStatus.APPLIED, RunStatus.FAILED},
    # Terminal states: no valid outbound transitions
    RunStatus.APPLIED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(String, ForeignKey("workspaces.id"), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String, nullable=True)  # user_id FK (Phase 3)
    # Legacy column from the 4-eyes era. 4-eyes was removed; new rows never
    # set this. Kept on the schema so historical rows from before the
    # changeover still load — drop in a follow-up migration once those are
    # archived.
    reviewer_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    command: Mapped[str] = mapped_column(String(20), nullable=False)  # plan, apply, destroy
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, values_callable=lambda enum: [e.value for e in enum]),
        default=RunStatus.PENDING,
    )
    # Captured at trigger time from workspace.repo_ref. Older runs (pre-012
    # migration) carry NULL — the dashboard treats those as "unknown branch"
    # and excludes them from per-branch last-run badges.
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured plan, captured by the executor via `terraform show -json tfplan`.
    # Used by the Approvals UI to render a resource-graph canvas before approve.
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Base64-encoded `tfplan` binary, stored after the plan phase succeeds and
    # restored by the apply phase (post-approval) so apply runs against the
    # exact same plan the approver reviewed. Two-phase deployment.
    tfplan_b64: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-run variable additions/overrides — JSON object encrypted as one
    # Fernet token. Decrypted once at executor launch, merged on top of
    # global+workspace layers, and replayed verbatim on the apply phase so
    # 4-eyes approvers can't see different values than the planner did.
    variables_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Auto-approval opt-ins set at run-create time. When the plan succeeds with
    # all gates green and a 0/0/0 summary, the worker posts a system approval
    # and (optionally) skips the apply phase. See migration 021.
    auto_approve_if_no_changes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    auto_approve_skip_apply: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    error_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OPA/conftest policy gate outcome, stamped by the executor's OPA Policy
    # Check step. `not_run` when the gate is off/skipped, `passed` when clean,
    # `warned` for advisory (warn-mode or warn/info severity) violations, and
    # `failed` when a `block` policy violated under enforce mode (which also
    # fails the run). Surfaced on the Runs list / Dashboard / run header.
    policy_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="not_run", default="not_run"
    )
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def transition(self, new_status: RunStatus) -> None:
        """Apply an FSM transition. Raises ValueError for invalid transitions."""
        allowed = _VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition: {self.status.value} → {new_status.value}"
            )
        self.status = new_status


class RunArtifact(Base):
    __tablename__ = "run_artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)  # plan_output, apply_output, checkov_report
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
