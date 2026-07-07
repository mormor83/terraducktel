"""Runs router: trigger, list, get details, patch status (executor / simulation)."""
import logging
import os
import smtplib
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

# IMPORTANT: import the module, not the symbol. Capturing `AsyncSessionLocal` at
# import time bypasses test-fixture monkeypatching of `app.db.AsyncSessionLocal`
# (the conftest._setup_db fixture rebinds the attribute). See test
# test_patch_run_uses_module_session_factory in test_patch_run_notification_resilience.py.
from app import db as _db
from app.auth.bu_context import BUScope, current_bu, scoped_run, scoped_workspace
from app.db import get_db
from app.auth.rbac import Role, require_role
from app.auth.jwt import create_access_token
from app.models.run import Run, RunStatus
from app.models.workspace import Workspace
from app.models.user import User
from app.schemas.run import RunCreate, RunResponse, RunUpdate
from app.schemas.run_step import RunStepResponse, RunStepUpdate
from app.services.notification_service import send_plan_approval_notification
from app.services import run_step_service as steps_svc
from app.services import api_key_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])


def _get_executor_service(db: AsyncSession):
    """Build an ExecutorService if Docker is available and enabled, else None.

    NOTE: Catch is intentionally narrow. RuntimeError from `get_credential_encryption_key()`
    (CREDENTIAL_ENCRYPTION_KEY unset) MUST propagate so misconfig surfaces as a 5xx
    instead of silently leaving runs in PENDING. See test_executor_misconfig.py.
    """
    if os.environ.get("EXECUTOR_ENABLED", "").lower() not in ("true", "1", "yes"):
        return None

    from app.auth.encryption_key import get_credential_encryption_key
    from app.services.config_service import ConfigService
    from app.services.executor_service import ExecutorService

    enc_key = get_credential_encryption_key()
    config_svc = ConfigService(db, enc_key)

    # ECS runtime: API container has no Docker daemon on Fargate. Skip the
    # `docker.from_env()` probe — `_launch_via_ecs` uses boto3, not self._docker.
    runtime = os.environ.get("EXECUTOR_RUNTIME", "docker").strip().lower()
    if runtime == "ecs":
        return ExecutorService(None, config_svc)

    try:
        import docker
        import docker.errors

        client = docker.from_env()
        return ExecutorService(client, config_svc)
    except (ImportError, docker.errors.DockerException):
        logger.debug("Docker not available — runs will stay in PENDING for manual executor", exc_info=True)
        return None


@router.post(
    "/api/v1/workspaces/{workspace_id}/runs",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_run(
    workspace_id: str,
    body: RunCreate,
    request: Request,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Trigger a plan/apply run on a workspace. Requires operator+ role."""
    ws = await scoped_workspace(workspace_id, bu, db)

    # API-key scope: a `plan` command needs the `plan` tier; `apply`/`destroy`
    # need the `apply` tier. The workspace must also be in the key's allowlist.
    # No-op for interactive (JWT) callers — require_role already governs them.
    api_key_service.enforce(
        request,
        need="plan" if body.command == "plan" else "apply",
        workspace_id=workspace_id,
    )

    # Branch override — persists to workspace.repo_ref so the dashboard chip,
    # drift detector, and subsequent runs all follow the chosen branch.
    # Validation happens against the post-override value so the 4-eyes check
    # below sees the right branch.
    if body.branch is not None:
        new_branch = body.branch.strip()
        if not new_branch:
            raise HTTPException(status_code=422, detail="branch must not be empty")
        ws.repo_ref = new_branch

    # Per-run variable blob: encrypt the list as a single Fernet token and
    # stash it on the run row. The executor will decrypt + merge it on top
    # of global/workspace layers at launch.
    from app.services import variable_service as varsvc

    variables_encrypted: str | None = None
    if body.variables:
        variables_encrypted = varsvc.serialize_run_variables(body.variables)

    # Auto-approve is only meaningful for commands that have an apply phase.
    # On `plan`-only runs the flag is silently dropped so clients can pass it
    # uniformly from automation without us 422-ing.
    auto_approve = bool(body.auto_approve_if_no_changes) and body.command in ("apply", "destroy")
    auto_skip_apply = bool(body.auto_approve_skip_apply) and auto_approve

    run = Run(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        triggered_by=current_user.id,
        command=body.command,
        status=RunStatus.PENDING,
        # Snapshot the workspace's current branch onto the run row so the
        # dashboard can show last-run-on-branch even after the operator
        # changes the branch between trigger and the apply phase.
        branch=ws.repo_ref,
        variables_encrypted=variables_encrypted,
        auto_approve_if_no_changes=auto_approve,
        auto_approve_skip_apply=auto_skip_apply,
    )
    # Pre-flight: catch encryption-key misconfig at trigger time so the operator
    # gets an immediate 5xx instead of a run that mysteriously goes to FAILED a
    # couple of seconds later. `_get_executor_service` lets RuntimeError from
    # `get_credential_encryption_key()` propagate; ImportError / DockerException
    # are tolerated (worker will retry / mark failed cleanly).
    if os.environ.get("EXECUTOR_ENABLED", "").lower() in ("true", "1", "yes"):
        _get_executor_service(db)  # raises if encryption key is unset

    db.add(run)
    await db.flush()

    # Seed canonical step list so the UI shows the timeline immediately. The
    # timeline shape depends on the workspace kind (terraform vs helm).
    await steps_svc.seed_steps(
        db, run.id, body.command, getattr(ws, "kind", "terraform") or "terraform"
    )

    # Enqueue rather than launch inline: the worker (app/services/run_worker.py)
    # claims the job within ~POLL_INTERVAL_SECONDS and spawns the executor.
    # Trigger handler stays fast even if Docker is slow / under load.
    from app.services.run_worker import enqueue_job

    await enqueue_job(db, run_id=run.id, phase="plan")

    await db.commit()
    await db.refresh(run)
    return run


@router.get("/api/v1/runs", response_model=list[RunResponse])
async def list_runs(
    request: Request,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """List runs, scoped to the caller's selected Business Unit.

    Superadmin with `X-Business-Unit: all` (or no header) sees every run
    across BUs. Otherwise only runs whose workspace belongs to the current BU.
    API keys are additionally narrowed to their workspace allowlist (if set).
    """
    stmt = select(Run).order_by(Run.created_at.desc())
    if bu.bu_id is not None:
        stmt = stmt.join(Workspace, Workspace.id == Run.workspace_id).where(
            Workspace.business_unit_id == bu.bu_id
        )
    allow = api_key_service.allowlist(request)
    if allow:
        stmt = stmt.where(Run.workspace_id.in_(allow))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/api/v1/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    request: Request,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Get run details. Requires viewer+ role and BU membership."""
    run = await scoped_run(run_id, bu, db)
    api_key_service.enforce(request, need="read", workspace_id=run.workspace_id)
    return run


_VALID_POLICY_STATUS = {"not_run", "passed", "warned", "failed"}


@router.patch("/api/v1/runs/{run_id}", response_model=RunResponse)
async def patch_run(
    run_id: str,
    body: RunUpdate,
    request: Request,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Update run status / plan output (simulates executor callbacks). Operator+.

    Ordering: DB commit MUST happen BEFORE notification dispatch. A transient Slack/SMTP
    outage must not roll back the FSM transition or surface a 5xx to the executor. See
    test_patch_run_notification_resilience.py.
    """
    run = await scoped_run(run_id, bu, db)
    # Enforce the API-key workspace allowlist + capability tier here, like every
    # sibling run endpoint — without this a workspace-scoped `plan`/`apply` key
    # could PATCH runs of OTHER workspaces in the BU and forge plan/policy state.
    # No-op for interactive JWT callers (the executor's own token).
    api_key_service.enforce(request, need="apply", workspace_id=run.workspace_id)

    if body.plan_output is not None:
        run.plan_output = body.plan_output
    if body.plan_json is not None:
        run.plan_json = body.plan_json
    if body.tfplan_b64 is not None:
        run.tfplan_b64 = body.tfplan_b64
    if body.policy_status is not None:
        # Constrain to the known set so a caller can't stamp an arbitrary
        # "passed"-looking value the dashboard/audit would trust.
        if body.policy_status not in _VALID_POLICY_STATUS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid policy_status: {body.policy_status}",
            )
        run.policy_status = body.policy_status

    notify_after_commit = False
    notification_payload: dict | None = None
    # Slack-bot notifications (per-BU). Each entry is a (kind, payload) tuple
    # dispatched after commit; kept separate from `notification_payload`
    # because the bot path is independent of the legacy webhook/email path
    # and can fire on more events (auto-approved, failed) where the legacy
    # path does not.
    slack_bot_events: list[tuple[str, dict]] = []

    if body.status is not None:
        try:
            new_status = RunStatus(body.status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status: {body.status}",
            )
        # H2: operator-PATCH cannot move into APPLYING — that path is reserved for
        # POST /api/v1/runs/{id}/approve which enforces the 4-eyes invariant.
        if new_status == RunStatus.APPLYING:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Use POST /approve to start apply (4-eyes invariant)",
            )
        # APPLIED is only legitimate after APPLYING (executor reporting completion).
        if new_status == RunStatus.APPLIED and run.status != RunStatus.APPLYING:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="APPLIED is only valid after APPLYING",
            )
        # Idempotent PATCH: if the status is already at the target, skip the
        # FSM transition. Without this, an executor that retries `running →
        # running` (e.g. after a transient reconnect) gets a 500 because the
        # FSM forbids self-loops.
        if new_status != run.status:
            run.transition(new_status)
        if new_status == RunStatus.AWAITING_APPROVAL:
            ws = await db.get(Workspace, run.workspace_id)
            # Look up the triggerer's email once so we can include it in
            # Slack messages. None on legacy rows (pre-Phase-3) without
            # `triggered_by` set; the notification just omits the field.
            triggerer_email: str | None = None
            if run.triggered_by:
                from app.models.user import User as _User

                triggerer = await db.get(_User, run.triggered_by)
                triggerer_email = triggerer.email if triggerer else None

            # Auto-approve gate: only fires when the run was created with
            # the opt-in flag, the executor produced a structured plan, and
            # the plan is 0/0/0. Gates other than "summary is no-changes"
            # are not re-checked here — the executor only PATCHes to
            # AWAITING_APPROVAL after checkov / cost pass.
            from app.services.plan_summary import summarize_plan_json

            plan_sum = summarize_plan_json(run.plan_json) if run.plan_json else None
            auto_approved = False
            if run.auto_approve_if_no_changes and plan_sum and plan_sum.is_no_changes:
                from app.services.approval_service import ApprovalService

                await ApprovalService().system_auto_approve(
                    db,
                    run,
                    summary={
                        "add": plan_sum.add,
                        "change": plan_sum.change,
                        "destroy": plan_sum.destroy,
                        "no_op": plan_sum.no_op,
                        "read": plan_sum.read,
                    },
                    skip_apply=run.auto_approve_skip_apply,
                )
                auto_approved = True
                if not run.auto_approve_skip_apply:
                    # Mirror the human-approve path: queue the apply
                    # phase so the worker runs `terraform apply tfplan`.
                    from app.services.run_worker import enqueue_job

                    await enqueue_job(db, run_id=run.id, phase="apply")
                slack_bot_events.append((
                    "auto_approved",
                    {
                        "workspace_id": run.workspace_id,
                        "workspace_name": ws.name if ws else run.workspace_id,
                        "run_id": run.id,
                        "skip_apply": bool(run.auto_approve_skip_apply),
                        "environment": ws.environment if ws else None,
                        "region": ws.region if ws else None,
                        "working_dir": ws.tf_working_dir if ws else None,
                        "branch": run.branch,
                        "command": run.command,
                        "triggered_by_email": triggerer_email,
                    },
                ))

            if not auto_approved:
                notify_after_commit = True
                notification_payload = {
                    "workspace_name": ws.name if ws else run.workspace_id,
                    "plan_output": run.plan_output or "",
                }
                slack_bot_events.append((
                    "awaiting_approval",
                    {
                        "workspace_id": run.workspace_id,
                        "workspace_name": ws.name if ws else run.workspace_id,
                        "run_id": run.id,
                        "environment": ws.environment if ws else None,
                        "region": ws.region if ws else None,
                        "working_dir": ws.tf_working_dir if ws else None,
                        "branch": run.branch,
                        "command": run.command,
                        "triggered_by_email": triggerer_email,
                        "add": plan_sum.add if plan_sum else None,
                        "change": plan_sum.change if plan_sum else None,
                        "destroy": plan_sum.destroy if plan_sum else None,
                    },
                ))
        elif new_status == RunStatus.FAILED:
            ws = await db.get(Workspace, run.workspace_id)
            triggerer_email = None
            if run.triggered_by:
                from app.models.user import User as _User

                triggerer = await db.get(_User, run.triggered_by)
                triggerer_email = triggerer.email if triggerer else None
            # Identify the failing stage from run_steps: pick the most
            # recent step in `failed` state for this run. Best-effort —
            # if we can't find one (e.g. failure before the first step
            # was seeded), the notification just omits the stage line.
            failed_stage: str | None = None
            try:
                from app.models.run_step import RunStep, StepStatus

                rs = await db.execute(
                    select(RunStep)
                    .where(RunStep.run_id == run.id, RunStep.status == StepStatus.FAILED)
                    .order_by(RunStep.position.desc())
                    .limit(1)
                )
                failed_step = rs.scalars().first()
                if failed_step:
                    failed_stage = failed_step.name
            except Exception:  # noqa: BLE001 — best-effort enrichment
                failed_stage = None
            slack_bot_events.append((
                "failed",
                {
                    "workspace_id": run.workspace_id,
                    "workspace_name": ws.name if ws else run.workspace_id,
                    "run_id": run.id,
                    "command": run.command,
                    "environment": ws.environment if ws else None,
                    "region": ws.region if ws else None,
                    "working_dir": ws.tf_working_dir if ws else None,
                    "branch": run.branch,
                    "triggered_by_email": triggerer_email,
                    "failed_stage": failed_stage,
                    "error_excerpt": (run.error_output or run.plan_output or "")[:600],
                },
            ))

    await db.commit()
    await db.refresh(run)

    if notify_after_commit and notification_payload is not None:
        # Best-effort. Catch transient outage exceptions ONLY — DB / decryption /
        # config errors are config bugs, not transient outages, and must surface
        # at ERROR (not WARNING) so operators see them.
        try:
            async with _db.AsyncSessionLocal() as notify_session:
                try:
                    await send_plan_approval_notification(
                        notify_session,
                        run.id,
                        notification_payload["workspace_name"],
                        notification_payload["plan_output"],
                    )
                except (httpx.RequestError, smtplib.SMTPException, OSError):
                    logger.warning(
                        "Transient notification outage for run %s",
                        run.id,
                        exc_info=True,
                    )
        except SQLAlchemyError:
            logger.error(
                "Notification session failed (DB / config error) for run %s",
                run.id,
                exc_info=True,
            )

    # Slack-bot dispatch — separate session so it can't roll back the FSM
    # transition. Failures are absorbed inside each helper, so we don't
    # wrap them again here.
    if slack_bot_events:
        from app.services.notification_service import (
            send_slack_run_auto_approved,
            send_slack_run_awaiting_approval,
            send_slack_run_failed,
        )

        async with _db.AsyncSessionLocal() as ns:
            for kind, payload in slack_bot_events:
                try:
                    if kind == "auto_approved":
                        await send_slack_run_auto_approved(ns, **payload)
                    elif kind == "awaiting_approval":
                        await send_slack_run_awaiting_approval(ns, **payload)
                    elif kind == "failed":
                        await send_slack_run_failed(ns, **payload)
                except Exception:  # noqa: BLE001 — best-effort
                    logger.warning(
                        "Slack-bot notification (%s) failed for run %s",
                        kind, run.id, exc_info=True,
                    )

    return run


@router.post("/api/v1/runs/{run_id}/heartbeat", status_code=204)
async def heartbeat_run(
    run_id: str,
    request: Request,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Executor liveness ping. Updates run_jobs.heartbeat_at so the reaper
    leaves this run alone. 204 on success; 404 if no picked job for this run.
    """
    from app.services.run_worker import heartbeat

    run = await scoped_run(run_id, bu, db)
    api_key_service.enforce(request, need="plan", workspace_id=run.workspace_id)
    ok = await heartbeat(db, run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="No picked job for this run")
    return None


@router.post("/api/v1/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: str,
    request: Request,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a run that has not yet been applied. Operator+.

    H1: closes the cancel-from-PLANNED gap. Allowed source states: PENDING,
    RUNNING, PLANNING, PLANNED, AWAITING_APPROVAL. Once APPLYING, runs cannot be
    cancelled — they must complete or fail.
    """
    run = await scoped_run(run_id, bu, db)
    api_key_service.enforce(request, need="plan", workspace_id=run.workspace_id)
    try:
        run.transition(RunStatus.CANCELLED)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    await db.commit()
    await db.refresh(run)
    return run


@router.get("/api/v1/runs/{run_id}/plan")
async def get_run_plan(
    run_id: str,
    request: Request,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Get the plan output for a run. Requires viewer+ role and BU membership."""
    run = await scoped_run(run_id, bu, db)
    api_key_service.enforce(request, need="read", workspace_id=run.workspace_id)
    return {"plan_output": run.plan_output}


@router.get("/api/v1/runs/{run_id}/tfplan")
async def get_run_tfplan(
    run_id: str,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Return the saved tfplan binary as base64 for the apply-phase executor.

    Operator+ only. The plan-phase executor PATCHes `tfplan_b64` onto the run
    row when terraform plan succeeds; the apply-phase executor fetches this
    endpoint, decodes it back to a `tfplan` file, and runs `terraform apply
    tfplan` against the exact plan the approver reviewed. Kept off the default
    `RunResponse` schema because the blob is large (~150 KB) and irrelevant to
    every other run consumer.
    """
    run = await scoped_run(run_id, bu, db)
    return {"tfplan_b64": run.tfplan_b64 or ""}


@router.get("/api/v1/runs/{run_id}/graph")
async def get_run_graph(
    run_id: str,
    request: Request,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Parse the captured `terraform show -json tfplan` into a node/edge graph.

    Returns `{nodes: [...], edges: [...], summary: {add, change, destroy}}`.
    Each node has `id`, `address`, `type` (e.g. `aws_s3_bucket`), `provider`,
    `change` (`create|update|delete|no-op|read|replace`), `module` (optional).
    Edges connect resources that explicitly reference each other in the plan
    (via `depends_on` or interpolation expressions surfaced as
    `before_sensitive`/`after_unknown`).
    """
    import json as _json

    run = await scoped_run(run_id, bu, db)
    api_key_service.enforce(request, need="read", workspace_id=run.workspace_id)
    if not run.plan_json:
        return {"nodes": [], "edges": [], "summary": {"add": 0, "change": 0, "destroy": 0}}

    try:
        plan = _json.loads(run.plan_json)
    except Exception:
        raise HTTPException(status_code=500, detail="plan_json is not valid JSON")

    nodes_by_addr: dict[str, dict] = {}
    edges: list[dict] = []
    summary = {"add": 0, "change": 0, "destroy": 0, "no_op": 0, "read": 0, "replace": 0}

    def _change_kind(actions: list[str]) -> str:
        a = set(actions or [])
        if a == {"no-op"}: return "no_op"
        if a == {"read"}: return "read"
        if "create" in a and "delete" in a: return "replace"
        if "create" in a: return "create"
        if "delete" in a: return "delete"
        if "update" in a: return "update"
        return "unknown"

    # Pass 1 — every resource that appears in the plan's resource_changes.
    # This covers create/update/delete and also any state row for which
    # terraform recorded a no-op (the actual existing state).
    for rc in plan.get("resource_changes", []) or []:
        addr = rc.get("address", "")
        if not addr:
            continue
        actions = rc.get("change", {}).get("actions", []) or []
        kind = _change_kind(actions)
        if kind == "create": summary["add"] += 1
        elif kind == "update": summary["change"] += 1
        elif kind == "delete": summary["destroy"] += 1
        elif kind == "replace": summary["destroy"] += 1; summary["add"] += 1
        elif kind == "no_op": summary["no_op"] += 1
        elif kind == "read": summary["read"] += 1
        nodes_by_addr[addr] = {
            "id": addr,
            "address": addr,
            "type": rc.get("type", ""),
            "name": rc.get("name", ""),
            "provider": (rc.get("provider_name") or "").replace(
                "registry.terraform.io/hashicorp/", ""
            ),
            "module": rc.get("module_address", ""),
            "change": kind,
            "mode": rc.get("mode", "managed"),
        }

    # Pass 2 — `prior_state.values.root_module` carries every existing resource
    # *currently* in state, including ones with no diff at all. Use this to
    # surface the full resource graph so the canvas shows the whole stack,
    # not just the resources Terraform plans to touch.
    def _walk_state(mod: dict, prefix: str = "") -> None:
        for r in mod.get("resources", []) or []:
            if r.get("mode") == "data":
                continue
            r_type = r.get("type", "")
            r_name = r.get("name", "")
            idx = r.get("index")
            local_addr = f"{r_type}.{r_name}"
            if idx is not None:
                if isinstance(idx, str):
                    local_addr += f'["{idx}"]'
                else:
                    local_addr += f"[{idx}]"
            full_addr = f"{prefix}.{local_addr}" if prefix else local_addr
            if full_addr not in nodes_by_addr:
                # Existing-but-unchanged resource — show as no_op.
                nodes_by_addr[full_addr] = {
                    "id": full_addr,
                    "address": full_addr,
                    "type": r_type,
                    "name": r_name,
                    "provider": (r.get("provider_name") or "").replace(
                        "registry.terraform.io/hashicorp/", ""
                    ),
                    "module": prefix,
                    "change": "no_op",
                    "mode": r.get("mode", "managed"),
                }
        for child in mod.get("child_modules", []) or []:
            child_addr = child.get("address", "")
            _walk_state(child, child_addr)

    prior_root = (
        plan.get("prior_state", {}).get("values", {}).get("root_module", {})
    )
    _walk_state(prior_root)

    # Pass 3 — extract real edges from `configuration.root_module`. Terraform
    # records every interpolated reference under each resource's
    # `expressions.<attr>.references[]`. These are the actual data-flow edges
    # (e.g. `module.foo.aws_subnet.bar.id` referenced by an `aws_instance`).
    def _walk_config(mod: dict, prefix: str = "") -> None:
        # Resource-to-resource edges from expressions.
        for r in mod.get("resources", []) or []:
            if r.get("mode") == "data":
                continue
            r_addr = (
                f"{prefix}.{r['type']}.{r['name']}" if prefix
                else f"{r['type']}.{r['name']}"
            )
            # depends_on
            for dep in r.get("depends_on", []) or []:
                dep_addr = f"{prefix}.{dep}" if prefix else dep
                _add_edge(dep_addr, r_addr, "depends_on")
            # interpolation references inside expressions
            for expr in (r.get("expressions") or {}).values():
                if isinstance(expr, dict):
                    for ref in expr.get("references", []) or []:
                        # Refs come as `aws_subnet.foo.id` — strip the trailing
                        # attribute selector and module-prefix it.
                        ref_addr = _ref_to_addr(ref, prefix)
                        if ref_addr:
                            _add_edge(ref_addr, r_addr, "ref")
                elif isinstance(expr, list):
                    for sub in expr:
                        if isinstance(sub, dict):
                            for ref in sub.get("references", []) or []:
                                ref_addr = _ref_to_addr(ref, prefix)
                                if ref_addr:
                                    _add_edge(ref_addr, r_addr, "ref")
        for sub in (mod.get("module_calls") or {}).values():
            sub_mod = sub.get("module", {})
            sub_name = sub.get("address") or ""
            sub_prefix = sub_name or (
                f"module.{sub.get('name', '?')}" if not prefix
                else f"{prefix}.module.{sub.get('name', '?')}"
            )
            # Edges from the module-call expressions to the consumer module.
            for expr in (sub.get("expressions") or {}).values():
                if isinstance(expr, dict):
                    for ref in expr.get("references", []) or []:
                        ref_addr = _ref_to_addr(ref, prefix)
                        if ref_addr:
                            # Edge to any resource at sub_prefix that references
                            # this attribute — too granular to compute here,
                            # so connect to the first node at sub_prefix.
                            for n in nodes_by_addr.values():
                                if n["module"] == sub_prefix:
                                    _add_edge(ref_addr, n["address"], "module_in")
                                    break

    seen_edges: set[tuple[str, str]] = set()

    def _add_edge(src: str, dst: str, kind: str) -> None:
        if src not in nodes_by_addr or dst not in nodes_by_addr:
            return
        if src == dst:
            return
        key = (src, dst)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"source": src, "target": dst, "kind": kind})

    def _ref_to_addr(ref: str, prefix: str) -> str:
        # Drop the trailing attribute selector ("aws_subnet.foo.id" → "aws_subnet.foo").
        if not ref or ref.startswith("var.") or ref.startswith("local.") or ref.startswith("each.") or ref.startswith("count.") or ref.startswith("path."):
            return ""
        parts = ref.split(".")
        if len(parts) >= 3 and parts[0] != "module":
            ref = ".".join(parts[:2])
        # Apply module prefix.
        return f"{prefix}.{ref}" if prefix and not ref.startswith("module.") else ref

    config = plan.get("configuration", {}).get("root_module", {})
    _walk_config(config)

    nodes = list(nodes_by_addr.values())
    return {"nodes": nodes, "edges": edges, "summary": summary}


@router.get("/api/v1/runs/{run_id}/steps", response_model=list[RunStepResponse])
async def list_run_steps(
    run_id: str,
    request: Request,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Per-step timeline for a run: status, started/completed, duration, output, summary."""
    run = await scoped_run(run_id, bu, db)
    api_key_service.enforce(request, need="read", workspace_id=run.workspace_id)
    rows = await steps_svc.list_steps(db, run_id)
    out: list[RunStepResponse] = []
    for s in rows:
        out.append(
            RunStepResponse(
                id=s.id,
                run_id=s.run_id,
                position=s.position,
                name=s.name,
                status=s.status.value if hasattr(s.status, "value") else str(s.status),
                started_at=s.started_at.isoformat() if s.started_at else None,
                completed_at=s.completed_at.isoformat() if s.completed_at else None,
                duration_seconds=s.duration_seconds,
                output=s.output,
                summary_json=s.summary_json,
            )
        )
    return out


@router.get("/api/v1/runs/{run_id}/policies")
async def get_run_policies(
    run_id: str,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """OPA policy bundle the executor should enforce for this run.

    Called by the executor (run-scoped API_TOKEN) during the OPA Policy Check
    step. Returns the per-BU gate config (mode, bundled/git sources + their
    severities) plus every enabled DB-authored policy's rego. The BU is derived
    from the run's own workspace, so the executor never needs a BU header.
    """
    from app.auth.encryption_key import get_credential_encryption_key
    from app.models.business_unit import BusinessUnit
    from app.routers import integrations as integ
    from app.services import policy_service
    from app.services.config_service import ConfigService

    run = await scoped_run(run_id, bu, db)
    ws = await db.get(Workspace, run.workspace_id)
    if ws is None or ws.business_unit_id is None:
        return {"mode": "off", "policies": []}

    bu_row = await db.get(BusinessUnit, ws.business_unit_id)
    slug = bu_row.slug if bu_row is not None else None
    svc = ConfigService(db, get_credential_encryption_key())

    async def _cfg(key: str) -> str | None:
        return await svc.get_for_bu(slug, key) if slug else await svc.get(key)

    mode = (await _cfg(integ.OPA_MODE_KEY) or "off").strip()
    if mode not in ("enforce", "warn", "off"):
        mode = "off"
    use_bundled = (await _cfg(integ.OPA_USE_BUNDLED_KEY) or "true").strip().lower() not in (
        "0", "false", "no", "off"
    )
    return {
        "mode": mode,
        "use_bundled": use_bundled,
        "bundled_severity": integ._norm_sev(await _cfg(integ.OPA_BUNDLED_SEVERITY_KEY)),
        "git_severity": integ._norm_sev(await _cfg(integ.OPA_GIT_SEVERITY_KEY)),
        "git": {
            "url": (await _cfg(integ.OPA_REPO_URL_KEY) or "").strip(),
            "ref": (await _cfg(integ.OPA_REPO_REF_KEY) or "main").strip(),
            "dir": (await _cfg(integ.OPA_REPO_DIR_KEY) or "").strip(),
        },
        "policies": await policy_service.bundle_for_run(db, ws.business_unit_id),
    }


@router.patch("/api/v1/runs/{run_id}/steps/{step_id}", response_model=RunStepResponse)
async def patch_run_step(
    run_id: str,
    step_id: str,
    body: RunStepUpdate,
    request: Request,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Executor-driven update for a single step (status, output, summary)."""
    from app.models.run_step import RunStep

    run = await scoped_run(run_id, bu, db)
    api_key_service.enforce(request, need="apply", workspace_id=run.workspace_id)
    step = await db.get(RunStep, step_id)
    if step is None or step.run_id != run_id:
        raise HTTPException(status_code=404, detail="Step not found")
    await steps_svc.update_step(
        db,
        step,
        status=body.status,
        output=body.output,
        summary_json=body.summary_json,
    )
    await db.commit()
    await db.refresh(step)
    return RunStepResponse(
        id=step.id,
        run_id=step.run_id,
        position=step.position,
        name=step.name,
        status=step.status.value if hasattr(step.status, "value") else str(step.status),
        started_at=step.started_at.isoformat() if step.started_at else None,
        completed_at=step.completed_at.isoformat() if step.completed_at else None,
        duration_seconds=step.duration_seconds,
        output=step.output,
        summary_json=step.summary_json,
    )
