"""The one way a Run is created.

Manual triggers (`POST /workspaces/{id}/runs`), push webhooks and environment
promotions all create runs through `create_run`, so every run gets the same
row shape, the same step timeline and the same queue entry — before this the
build-Run / seed-steps / enqueue sequence was copied four times and one copy
(the old environments promote) forgot to enqueue.

Hard rule for promotions: a promotion run is an ordinary gated run. Passing a
`promotion_id` together with any auto-approve flag is a programming error and
raises — no caller can turn a promotion into a self-approving apply.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run, RunStatus
from app.models.workspace import Workspace
from app.services import run_step_service as steps_svc

# Commands the pipeline understands. `refresh` = `plan -refresh-only`, then the
# same approval pause and `apply` of the saved (refresh-only) plan.
COMMANDS = ("plan", "apply", "destroy", "refresh")
# Commands that pause for approval and have an apply phase.
APPLYING_COMMANDS = ("apply", "destroy", "refresh")


async def create_run(
    db: AsyncSession,
    ws: Workspace,
    *,
    command: str,
    triggered_by: str,
    branch: Optional[str] = None,
    variables_encrypted: Optional[str] = None,
    auto_approve_if_no_changes: bool = False,
    auto_approve_skip_apply: bool = False,
    promotion_id: Optional[str] = None,
) -> Run:
    """Create a PENDING run, seed its steps and queue its plan phase. Caller commits."""
    if command not in COMMANDS:
        raise ValueError(f"Unknown run command: {command}")
    if promotion_id is not None and (auto_approve_if_no_changes or auto_approve_skip_apply):
        raise ValueError("Promotion runs can never auto-approve")
    kind = getattr(ws, "kind", "terraform") or "terraform"
    if command == "refresh" and kind != "terraform":
        raise ValueError("refresh is only supported for terraform workspaces")

    # Auto-approve only means something for commands with an apply phase.
    auto_approve = bool(auto_approve_if_no_changes) and command in ("apply", "destroy")
    auto_skip = bool(auto_approve_skip_apply) and auto_approve

    run = Run(
        id=str(uuid.uuid4()),
        workspace_id=ws.id,
        triggered_by=triggered_by,
        command=command,
        status=RunStatus.PENDING,
        # Snapshot the branch so a later branch change on the workspace can't
        # re-point a run that is already in flight.
        branch=branch or ws.repo_ref,
        variables_encrypted=variables_encrypted,
        auto_approve_if_no_changes=auto_approve,
        auto_approve_skip_apply=auto_skip,
        promotion_id=promotion_id,
    )
    db.add(run)
    await db.flush()
    await steps_svc.seed_steps(db, run.id, command, kind)

    # Enqueue rather than launch inline: the worker claims the job and spawns
    # the executor, so the caller stays fast even if Docker is slow.
    from app.services.run_worker import enqueue_job

    await enqueue_job(db, run_id=run.id, phase="plan")
    return run
