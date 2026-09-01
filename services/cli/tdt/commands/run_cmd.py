"""`tdt run …` — trigger, watch, gate and approve runs.

`tdt run apply --wait` is the reason this CLI exists. It collapses the
25-line curl-and-poll loop the skill used to carry into one command, and — more
importantly — moves the approval safety check out of prose an agent has to
remember and into flags the CLI enforces:

    tdt run apply vpc --wait --auto-approve --max-destroy 0

"Approve only if nothing gets destroyed" stops being an instruction and becomes
a guarantee.
"""
from __future__ import annotations

import sys
import time

import typer

from ..errors import ExitCode, TdtError
from ..output import Fmt, age, dump_json, echo, ok, render
from ..state import AppCtx
from .ws_cmd import resolve_workspace

app = typer.Typer(no_args_is_help=True, help="Runs: trigger, watch, approve, inspect.")

TERMINAL_OK = {"applied", "planned"}
TERMINAL_BAD = {"failed", "cancelled"}
TERMINAL_STEP = {"success", "failed", "skipped"}

_RUN_COLUMNS = [
    ("ID", "id"),
    ("WORKSPACE", "workspace"),
    ("CMD", "command"),
    ("STATUS", "status"),
    ("AGE", "age"),
]


def _apply_filters_locally(runs: list[dict], params: dict) -> list[dict]:
    """Re-apply `workspace_id` / `status` / `limit` to a response.

    Idempotent against a server that already filtered. Ordering is the server's
    (`created_at desc`), so slicing to `limit` keeps "newest N" either way.
    """
    workspace_id = params.get("workspace_id")
    if workspace_id:
        runs = [r for r in runs if str(r.get("workspace_id")) == str(workspace_id)]
    status = params.get("status")
    if status:
        wanted = {v.strip() for v in str(status).split(",") if v.strip()}
        runs = [r for r in runs if str(r.get("status")) in wanted]
    limit = params.get("limit")
    if limit:
        runs = runs[: int(limit)]
    return runs


def _run_rows(obj: AppCtx, runs: list[dict]) -> list[dict]:
    """Table view: swap workspace_id for its name and trim the ISO timestamp.

    JSON output is never reshaped this way — `-o json` stays a faithful echo of
    the API payload.
    """
    names = {str(w.get("id")): w.get("name") for w in (obj.client.get("/workspaces") or [])}
    return [
        {
            **r,
            "workspace": names.get(str(r.get("workspace_id")), r.get("workspace_id")),
            "age": age(r.get("created_at")),
        }
        for r in runs
    ]


class _StepStreamer:
    """Prints step transitions once each, using the `since` cursor.

    Holds a cursor at the first not-yet-terminal step and asks the API only for
    that position onwards with `include_output=false`, so a long apply doesn't
    re-download every finished step's terraform output on every poll.
    """

    def __init__(self, client, run_id: str, quiet: bool):
        self._client = client
        self._run_id = run_id
        self._quiet = quiet
        self._cursor = 0
        self._announced: set[int] = set()

    def poll(self) -> list[dict]:
        steps = self._client.get(
            f"/runs/{self._run_id}/steps",
            params={"since": self._cursor, "include_output": "false"},
        ) or []
        for step in steps:
            pos, status = step.get("position"), step.get("status")
            if status in TERMINAL_STEP and pos not in self._announced:
                self._announced.add(pos)
                if not self._quiet:
                    mark = {"success": "[green]✓[/green]", "failed": "[red]✗[/red]"}.get(
                        status, "[dim]-[/dim]"
                    )
                    secs = step.get("duration_seconds")
                    suffix = f" [dim]{secs}s[/dim]" if secs else ""
                    echo(f"  {mark} {step.get('name')}{suffix}")
        # Advance past the leading run of terminal steps only — a later step can
        # still be pending while an earlier one runs, and we must not skip it.
        for step in sorted(steps, key=lambda s: s.get("position", 0)):
            if step.get("status") in TERMINAL_STEP and step.get("position") == self._cursor:
                self._cursor += 1
            else:
                break
        return steps

    def failed_step(self) -> dict | None:
        """Re-fetch with output so the real error can be shown."""
        steps = self._client.get(
            f"/runs/{self._run_id}/steps", params={"include_output": "true"}
        ) or []
        for step in steps:
            if step.get("status") == "failed":
                return step
        return None


def _summary(client, run_id: str) -> dict:
    graph = client.get(f"/runs/{run_id}/graph") or {}
    return graph.get("summary") or {}


def _fmt_summary(summary: dict) -> str:
    return (
        f"+{summary.get('add', 0)} "
        f"~{summary.get('change', 0)} "
        f"-{summary.get('destroy', 0)} "
        f"±{summary.get('replace', 0)}"
    )


def _print_diff(client, run_id: str) -> dict:
    graph = client.get(f"/runs/{run_id}/graph") or {}
    summary = graph.get("summary") or {}
    echo(f"\n  [bold]Plan:[/bold] {_fmt_summary(summary)}")
    changed = [
        n for n in (graph.get("nodes") or [])
        if n.get("change") not in (None, "no-op", "no_op")
    ]
    for node in changed[:60]:
        change = str(node.get("change"))
        colour = {
            "create": "green", "add": "green",
            "update": "yellow", "change": "yellow",
            "delete": "red", "destroy": "red",
            "replace": "magenta",
        }.get(change, "white")
        echo(f"    [{colour}]{change:>8}[/{colour}]  {node.get('address')}")
    if len(changed) > 60:
        echo(f"    [dim]… and {len(changed) - 60} more[/dim]")
    echo("")
    return summary


def _check_gates(
    summary: dict,
    *,
    require_noop: bool,
    max_add: int | None,
    max_change: int | None,
    max_destroy: int | None,
    max_replace: int | None,
) -> list[str]:
    """Return the list of violated gates — empty means the plan is acceptable."""
    add = int(summary.get("add", 0) or 0)
    change = int(summary.get("change", 0) or 0)
    destroy = int(summary.get("destroy", 0) or 0)
    replace = int(summary.get("replace", 0) or 0)

    violations: list[str] = []
    if require_noop and (add or change or destroy or replace):
        violations.append(f"--require-noop but plan is {_fmt_summary(summary)}")
    for label, value, cap in (
        ("add", add, max_add),
        ("change", change, max_change),
        ("destroy", destroy, max_destroy),
        ("replace", replace, max_replace),
    ):
        if cap is not None and value > cap:
            violations.append(f"--max-{label}={cap} but plan {label}s {value}")
    return violations


def _await_run(
    obj: AppCtx,
    run_id: str,
    *,
    command: str,
    auto_approve: bool,
    require_noop: bool,
    max_add: int | None,
    max_change: int | None,
    max_destroy: int | None,
    max_replace: int | None,
    timeout: int,
    poll: int,
) -> str:
    """Poll a run to a terminal state, gating the approval pause. Returns status."""
    client = obj.client
    quiet = obj.fmt is Fmt.json
    streamer = _StepStreamer(client, run_id, quiet)
    approved = False
    deadline = time.time() + timeout

    while True:
        if time.time() > deadline:
            raise TdtError(
                f"Timed out after {timeout}s waiting on run {run_id}.",
                ExitCode.TIMEOUT,
                hint=f"The run is still going server-side: tdt run watch {run_id}",
            )

        run = client.get(f"/runs/{run_id}") or {}
        status = str(run.get("status"))
        streamer.poll()

        if status == "awaiting_approval" and not approved:
            summary = _print_diff(client, run_id) if not quiet else _summary(client, run_id)

            violations = _check_gates(
                summary,
                require_noop=require_noop,
                max_add=max_add,
                max_change=max_change,
                max_destroy=max_destroy,
                max_replace=max_replace,
            )
            if violations:
                client.post(f"/runs/{run_id}/reject", {"comment": "Rejected by tdt CLI gate"})
                raise TdtError(
                    "Plan refused by gate: " + "; ".join(violations),
                    ExitCode.REJECTED,
                    hint="The run was rejected. Nothing was applied.",
                )

            if auto_approve:
                client.post(f"/runs/{run_id}/approve", {"comment": "Auto-approved by tdt CLI"})
            elif sys.stdin.isatty():
                choice = typer.prompt("  [A]pprove / [R]eject / [Q]uit", default="q").strip().lower()
                if choice.startswith("a"):
                    client.post(f"/runs/{run_id}/approve", {"comment": "Approved via tdt CLI"})
                elif choice.startswith("r"):
                    client.post(f"/runs/{run_id}/reject", {"comment": "Rejected via tdt CLI"})
                    raise TdtError("Rejected at the approval prompt.", ExitCode.REJECTED)
                else:
                    raise TdtError(
                        f"Left run {run_id} awaiting approval.",
                        ExitCode.REJECTED,
                        hint=f"Resume later: tdt run approve {run_id}",
                    )
            else:
                raise TdtError(
                    "Run is awaiting approval but there is no TTY to prompt on.",
                    ExitCode.REJECTED,
                    hint=(
                        "Re-run with --auto-approve plus a gate, e.g.\n"
                        "  --auto-approve --max-destroy 0"
                    ),
                )
            approved = True

        if status in TERMINAL_OK:
            return status
        if status in TERMINAL_BAD:
            step = streamer.failed_step()
            if step and not quiet:
                echo(f"\n[red]Failed step:[/red] {step.get('name')}")
                output = (step.get("output") or "").strip()
                if output:
                    tail = output.splitlines()[-40:]
                    echo("[dim]" + "\n".join(tail) + "[/dim]")
            # Before approval the plan phase was at fault; after it, the apply.
            raise TdtError(
                f"Run {run_id} ended {status}.",
                ExitCode.APPLY_FAILED if approved else ExitCode.PLAN_FAILED,
            )

        time.sleep(poll)


def _trigger(
    obj: AppCtx,
    workspace: str,
    command: str,
    branch: str | None,
) -> tuple[dict, str]:
    ws = resolve_workspace(obj, workspace)
    if branch and branch != ws.get("repo_ref"):
        obj.client.put(f"/workspaces/{ws['id']}", {"repo_ref": branch})
        if obj.fmt is Fmt.table:
            echo(f"[dim]Pinned {ws['name']} to '{branch}'.[/dim]")
    run = obj.client.post(f"/workspaces/{ws['id']}/runs", {"command": command})
    run_id = str(run.get("id"))
    if obj.fmt is Fmt.table:
        echo(f"[bold]{command}[/bold] {ws['name']} → run {run_id}")
    return ws, run_id


# ─── commands ──────────────────────────────────────────────────────────────


@app.command("list")
def run_list(
    ctx: typer.Context,
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Id or name."),
    status: str | None = typer.Option(
        None, "--status", help="Comma-separated, e.g. failed,cancelled."
    ),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Newest N only."),
) -> None:
    """List runs, narrowed server-side."""
    obj: AppCtx = ctx.obj
    params: dict = {}
    if workspace:
        params["workspace_id"] = resolve_workspace(obj, workspace)["id"]
    if status:
        params["status"] = status
    if limit:
        params["limit"] = limit
    runs = obj.client.get("/runs", params=params or None) or []
    # Belt for version skew: an API build without these query params ignores them
    # and returns everything, which would make `--limit 5` silently print
    # hundreds of rows. Re-applying the same predicates client-side is a no-op
    # when the server did filter, and keeps the flags honest when it did not.
    runs = _apply_filters_locally(runs, params)
    if obj.fmt is Fmt.json:
        dump_json(runs)
        return
    render(obj.fmt, _run_rows(obj, runs), _RUN_COLUMNS, empty="No runs.")


@app.command("get")
def run_get(ctx: typer.Context, run_id: str = typer.Argument(...)) -> None:
    """Show one run."""
    obj: AppCtx = ctx.obj
    render(obj.fmt, obj.client.get(f"/runs/{run_id}"))


@app.command("steps")
def run_steps(
    ctx: typer.Context,
    run_id: str = typer.Argument(...),
    since: int = typer.Option(0, "--since", help="Only steps at position >= this."),
    logs: bool = typer.Option(
        False, "--logs", help="Include each step's log output.",
    ),
) -> None:
    """Per-step timeline for a run.

    The flag is `--logs`, not `--output`: `-o/--output` is the global
    table-vs-json switch, and having both mean different things one word apart
    was a trap.
    """
    obj: AppCtx = ctx.obj
    steps = obj.client.get(
        f"/runs/{run_id}/steps",
        params={"since": since, "include_output": str(logs).lower()},
    )
    if obj.fmt is Fmt.json:
        dump_json(steps)
        return
    render(
        obj.fmt,
        steps,
        [("#", "position"), ("STEP", "name"), ("STATUS", "status"), ("SECS", "duration_seconds")],
        empty="No steps.",
    )
    if logs:
        for step in steps or []:
            if step.get("output"):
                echo(f"\n[bold]{step['name']}[/bold]")
                echo(f"[dim]{step['output']}[/dim]")


@app.command("graph")
def run_graph(ctx: typer.Context, run_id: str = typer.Argument(...)) -> None:
    """Plan diff: the +/~/-/± summary and the changed addresses."""
    obj: AppCtx = ctx.obj
    if obj.fmt is Fmt.json:
        dump_json(obj.client.get(f"/runs/{run_id}/graph"))
        return
    _print_diff(obj.client, run_id)


@app.command("plan-output")
def run_plan_output(ctx: typer.Context, run_id: str = typer.Argument(...)) -> None:
    """Raw terraform plan text for a run."""
    obj: AppCtx = ctx.obj
    data = obj.client.get(f"/runs/{run_id}/plan")
    if obj.fmt is Fmt.json:
        dump_json(data)
        return
    echo(data if isinstance(data, str) else str(data.get("plan_output") or data))


@app.command("approve")
def run_approve(
    ctx: typer.Context,
    run_id: str = typer.Argument(...),
    comment: str = typer.Option("Approved via tdt CLI", "--comment"),
) -> None:
    """Approve a run parked at awaiting_approval."""
    obj: AppCtx = ctx.obj
    obj.client.post(f"/runs/{run_id}/approve", {"comment": comment})
    ok(f"Approved run {run_id}.")


@app.command("reject")
def run_reject(
    ctx: typer.Context,
    run_id: str = typer.Argument(...),
    comment: str = typer.Option("Rejected via tdt CLI", "--comment"),
) -> None:
    """Reject a run parked at awaiting_approval."""
    obj: AppCtx = ctx.obj
    obj.client.post(f"/runs/{run_id}/reject", {"comment": comment})
    ok(f"Rejected run {run_id}.")


@app.command("cancel")
def run_cancel(ctx: typer.Context, run_id: str = typer.Argument(...)) -> None:
    """Cancel an in-flight run."""
    obj: AppCtx = ctx.obj
    obj.client.post(f"/runs/{run_id}/cancel")
    ok(f"Cancelled run {run_id}.")


@app.command("watch")
def run_watch(
    ctx: typer.Context,
    run_id: str = typer.Argument(...),
    timeout: int = typer.Option(3600, "--timeout"),
    poll: int = typer.Option(5, "--poll"),
    auto_approve: bool = typer.Option(False, "--auto-approve"),
    max_destroy: int | None = typer.Option(None, "--max-destroy"),
) -> None:
    """Follow an existing run to completion."""
    obj: AppCtx = ctx.obj
    status = _await_run(
        obj, run_id, command="watch", auto_approve=auto_approve,
        require_noop=False, max_add=None, max_change=None,
        max_destroy=max_destroy, max_replace=None,
        timeout=timeout, poll=poll,
    )
    ok(f"Run {run_id} → {status}.")


@app.command("plan")
def run_plan(
    ctx: typer.Context,
    workspace: str = typer.Argument(..., help="Workspace id or name."),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Pin this ref first."),
    wait: bool = typer.Option(True, "--wait/--no-wait"),
    timeout: int = typer.Option(1800, "--timeout"),
    poll: int = typer.Option(5, "--poll"),
) -> None:
    """Trigger a plan. Ends at `planned` — no approval involved."""
    obj: AppCtx = ctx.obj
    _, run_id = _trigger(obj, workspace, "plan", branch)
    if not wait:
        render(obj.fmt, {"run_id": run_id})
        return
    status = _await_run(
        obj, run_id, command="plan", auto_approve=False, require_noop=False,
        max_add=None, max_change=None, max_destroy=None, max_replace=None,
        timeout=timeout, poll=poll,
    )
    if obj.fmt is Fmt.json:
        dump_json({"run_id": run_id, "status": status, "summary": _summary(obj.client, run_id)})
    else:
        _print_diff(obj.client, run_id)
        ok(f"Run {run_id} → {status}.")


@app.command("destroy")
def run_destroy(
    ctx: typer.Context,
    workspace: str = typer.Argument(..., help="Workspace id or name."),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Pin this ref first."),
    wait: bool = typer.Option(True, "--wait/--no-wait"),
    auto_approve: bool = typer.Option(
        False, "--auto-approve",
        help="Approve without prompting. --max-destroy is still enforced.",
    ),
    max_destroy: int | None = typer.Option(
        None, "--max-destroy",
        help="Refuse if the plan would destroy more than this many resources.",
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Skip the typed-name confirmation prompt.",
    ),
    timeout: int = typer.Option(3600, "--timeout"),
    poll: int = typer.Option(5, "--poll"),
) -> None:
    """Run a real `terraform destroy` against the workspace.

    **This is the only command here that deletes infrastructure.** `tdt ws delete`
    merely drops TDT's tracking row; this tears down the resources.

    On a TTY it asks you to type the workspace name before triggering — a
    mistyped id on the other commands is recoverable, here it is not. Use
    `--yes` in automation, and prefer pairing it with `--max-destroy N` so an
    unexpectedly large plan is refused rather than approved.
    """
    obj: AppCtx = ctx.obj
    ws = resolve_workspace(obj, workspace)

    if not yes:
        if not sys.stdin.isatty():
            raise TdtError(
                "Refusing to destroy without confirmation and without a TTY.",
                ExitCode.USAGE,
                hint="Pass --yes to confirm in automation (ideally with --max-destroy N).",
            )
        echo(
            f"\n[red]This runs `terraform destroy` on[/red] [bold]{ws['name']}[/bold] "
            f"[dim]({ws.get('environment')} · {ws.get('aws_account_id')} · "
            f"{ws.get('tf_working_dir')})[/dim]"
        )
        typed = typer.prompt("Type the workspace name to confirm", default="").strip()
        if typed != ws["name"]:
            raise TdtError("Name did not match — nothing was destroyed.", ExitCode.REJECTED)

    _, run_id = _trigger(obj, workspace, "destroy", branch)
    if not wait:
        render(obj.fmt, {"run_id": run_id})
        return
    status = _await_run(
        obj, run_id, command="destroy", auto_approve=auto_approve,
        require_noop=False, max_add=None, max_change=None,
        max_destroy=max_destroy, max_replace=None,
        timeout=timeout, poll=poll,
    )
    if obj.fmt is Fmt.json:
        dump_json({"run_id": run_id, "status": status})
    else:
        ok(f"Run {run_id} → {status}.")


@app.command("apply")
def run_apply(
    ctx: typer.Context,
    workspace: str = typer.Argument(..., help="Workspace id or name."),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Pin this ref first."),
    wait: bool = typer.Option(True, "--wait/--no-wait"),
    auto_approve: bool = typer.Option(
        False, "--auto-approve",
        help="Approve without prompting. Gates below are still enforced.",
    ),
    require_noop: bool = typer.Option(
        False, "--require-noop", help="Refuse unless the plan is 0/0/0/0.",
    ),
    max_add: int | None = typer.Option(None, "--max-add", help="Refuse if adds exceed this."),
    max_change: int | None = typer.Option(None, "--max-change", help="Refuse if changes exceed this."),
    max_destroy: int | None = typer.Option(None, "--max-destroy", help="Refuse if destroys exceed this."),
    max_replace: int | None = typer.Option(None, "--max-replace", help="Refuse if replaces exceed this."),
    timeout: int = typer.Option(3600, "--timeout"),
    poll: int = typer.Option(5, "--poll"),
) -> None:
    """Plan, gate, approve and apply in one command.

    Gates are checked whether or not you passed --auto-approve, and a violated
    gate *rejects* the run — nothing is applied. Without --auto-approve on a
    TTY you get the interactive prompt; without a TTY you get exit code 4
    rather than a hang.
    """
    obj: AppCtx = ctx.obj
    _, run_id = _trigger(obj, workspace, "apply", branch)
    if not wait:
        render(obj.fmt, {"run_id": run_id})
        return
    status = _await_run(
        obj, run_id, command="apply", auto_approve=auto_approve,
        require_noop=require_noop, max_add=max_add, max_change=max_change,
        max_destroy=max_destroy, max_replace=max_replace,
        timeout=timeout, poll=poll,
    )
    if obj.fmt is Fmt.json:
        dump_json({"run_id": run_id, "status": status})
    else:
        ok(f"Run {run_id} → {status}.")
