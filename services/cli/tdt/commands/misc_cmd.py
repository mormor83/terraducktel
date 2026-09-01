"""Smaller command groups: drift, the read-only registries, `context`, `skill`."""
from __future__ import annotations

import shutil
from pathlib import Path

import typer

from ..errors import ExitCode, TdtError
from ..output import Fmt, dump_json, echo, ok, render
from ..state import AppCtx

drift_app = typer.Typer(no_args_is_help=True, help="Drift reports.")
show_app = typer.Typer(no_args_is_help=True, help="Read-only registries: BUs, accounts, clusters.")
skill_app = typer.Typer(no_args_is_help=True, help="Manage the bundled Claude skill.")


# ─── drift ─────────────────────────────────────────────────────────────────


@drift_app.command("summary")
def drift_summary(ctx: typer.Context) -> None:
    """Drift status across the BU."""
    obj: AppCtx = ctx.obj
    render(obj.fmt, obj.client.get("/drift/summary"))


@drift_app.command("get")
def drift_get(ctx: typer.Context, workspace: str = typer.Argument(...)) -> None:
    """The latest drift report for one workspace."""
    from .ws_cmd import resolve_workspace

    obj: AppCtx = ctx.obj
    ws = resolve_workspace(obj, workspace)
    render(obj.fmt, obj.client.get(f"/drift/{ws['id']}"))


@drift_app.command("scan")
def drift_scan(ctx: typer.Context, workspace: str = typer.Argument(...)) -> None:
    """Queue a fresh drift scan (read-only plan) for one workspace."""
    from .ws_cmd import resolve_workspace

    obj: AppCtx = ctx.obj
    ws = resolve_workspace(obj, workspace)
    obj.client.post(f"/drift/{ws['id']}/scan", allow_status=(202,))
    ok(f"Queued a drift scan for {ws['name']}.")


# ─── registries ────────────────────────────────────────────────────────────


@show_app.command("bus")
def list_bus(ctx: typer.Context) -> None:
    """Business units you can see — the source of truth for --bu slugs."""
    obj: AppCtx = ctx.obj
    render(
        obj.fmt,
        obj.client.get("/business-units"),
        [("ID", "id"), ("SLUG", "slug"), ("NAME", "name")],
        empty="No business units visible.",
    )


@show_app.command("accounts")
def list_accounts(ctx: typer.Context) -> None:
    """AWS accounts registered in the current BU."""
    obj: AppCtx = ctx.obj
    render(
        obj.fmt,
        obj.client.get("/aws-accounts"),
        [("ID", "id"), ("ACCOUNT", "account_id"), ("NAME", "name"), ("BUCKET", "state_bucket")],
        empty="No AWS accounts in this BU.",
    )


@show_app.command("clusters")
def list_clusters(ctx: typer.Context) -> None:
    """Kubernetes clusters registered in the current BU (for kind=helm workspaces)."""
    obj: AppCtx = ctx.obj
    render(
        obj.fmt,
        obj.client.get("/clusters"),
        [("ID", "id"), ("NAME", "name"), ("ENDPOINT", "endpoint")],
        empty="No clusters in this BU.",
    )


# ─── context ───────────────────────────────────────────────────────────────


_CLEAN_DRIFT = {"clean", "in_sync", "unknown", None}


def _drift_digest(summary: dict | None) -> dict | None:
    """Reduce /drift/summary to counts plus the workspaces that actually drifted.

    The raw payload carries a per-workspace row for every workspace in the BU,
    which for a real deployment is most of the response by volume and none of
    the signal. `context` exists to be small.
    """
    if not summary:
        return None
    rows = summary.get("by_workspace") or []
    drifted = [
        {
            "name": r.get("name"),
            "drift_status": r.get("drift_status"),
            "modified": r.get("modified_count"),
            "untracked": r.get("untracked_count"),
            "deleted": r.get("deleted_count"),
        }
        for r in rows
        if r.get("drift_status") not in _CLEAN_DRIFT
    ]
    return {
        k: v for k, v in summary.items() if k != "by_workspace"
    } | {"drifted": drifted}


def context(
    ctx: typer.Context,
    runs: int = typer.Option(10, "--runs", help="How many recent runs to include."),
) -> None:
    """One-shot snapshot of the BU: workspaces, recent runs, drift.

    Built for an agent's first call — it replaces four or five separate GETs, and
    defaults to JSON because that is what consumes it.
    """
    obj: AppCtx = ctx.obj
    client = obj.client

    workspaces = client.get("/workspaces") or []
    recent = client.get("/runs", params={"limit": runs}) or []
    try:
        drift = _drift_digest(client.get("/drift/summary"))
    except TdtError:
        drift = None  # drift-detector may not be deployed; not fatal for context

    names = {str(w.get("id")): w.get("name") for w in workspaces}
    snapshot = {
        "profile": {"name": obj.profile.name, "url": obj.profile.url, "bu": obj.profile.bu},
        "workspaces": [
            {
                "id": w.get("id"),
                "name": w.get("name"),
                "environment": w.get("environment"),
                "aws_account_id": w.get("aws_account_id"),
                "region": w.get("region"),
                "repo_ref": w.get("repo_ref"),
                "tf_working_dir": w.get("tf_working_dir"),
                "kind": w.get("kind"),
            }
            for w in workspaces
        ],
        "recent_runs": [
            {
                "id": r.get("id"),
                "workspace": names.get(str(r.get("workspace_id")), r.get("workspace_id")),
                "command": r.get("command"),
                "status": r.get("status"),
                "branch": r.get("branch"),
                "created_at": r.get("created_at"),
            }
            for r in recent
        ],
        "drift": drift,
    }

    if obj.fmt is Fmt.table:
        shown = snapshot["workspaces"][:15]
        echo(f"[bold]{len(snapshot['workspaces'])}[/bold] workspaces, "
             f"[bold]{len(snapshot['recent_runs'])}[/bold] recent runs "
             f"in BU '{obj.profile.bu or '(default)'}'")
        render(
            obj.fmt,
            shown,
            [("NAME", "name"), ("ENV", "environment"), ("REF", "repo_ref"), ("DIR", "tf_working_dir")],
        )
        if len(snapshot["workspaces"]) > len(shown):
            echo(f"[dim]… and {len(snapshot['workspaces']) - len(shown)} more "
                 f"(tdt ws list for all)[/dim]")
        echo("")
        render(
            obj.fmt,
            snapshot["recent_runs"],
            [("ID", "id"), ("WORKSPACE", "workspace"), ("CMD", "command"), ("STATUS", "status")],
        )
        echo("\n[dim]Pass -o json for the machine-readable snapshot.[/dim]")
    else:
        dump_json(snapshot)


# ─── skill ─────────────────────────────────────────────────────────────────


def _bundled_skill() -> Path:
    path = Path(__file__).resolve().parent.parent / "skill_asset" / "SKILL.md"
    if not path.exists():
        raise TdtError("This build has no bundled SKILL.md.", ExitCode.API)
    return path


@skill_app.command("install")
def skill_install(
    target: Path = typer.Option(
        Path.home() / ".claude" / "skills" / "tdt",
        "--target",
        help="Skill directory to write into.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing SKILL.md."),
) -> None:
    """Install the SKILL.md that ships with this CLI version.

    The skill is versioned *with* the binary on purpose: the old hand-maintained
    copy drifted from the API (it documented endpoints that had moved and missed
    ones that existed). Reinstalling after a CLI upgrade is how it stays true.

    You do not need this inside a terraducktel checkout — the repo carries the
    same skill at `.claude/skills/tdt/`, so a clone picks it up already. This is
    for using `tdt` against TDT from anywhere else.
    """
    src = _bundled_skill()
    target.mkdir(parents=True, exist_ok=True)
    dest = target / "SKILL.md"
    if dest.exists() and not force:
        raise TdtError(
            f"{dest} already exists.", ExitCode.USAGE, hint="Re-run with --force to replace it."
        )
    shutil.copyfile(src, dest)
    ok(f"Installed skill to {dest}")


@skill_app.command("show")
def skill_show() -> None:
    """Print the bundled SKILL.md."""
    import sys

    sys.stdout.write(_bundled_skill().read_text())
