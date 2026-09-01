"""`tdt ws …` — workspaces."""
from __future__ import annotations

import typer

from ..errors import ExitCode, TdtError
from ..output import Fmt, echo, ok, render
from ..state import AppCtx

app = typer.Typer(no_args_is_help=True, help="Workspaces: list, inspect, import, sync.")

# No ID column on purpose: two UUIDs per row is unreadable at 80 columns, and
# every command that takes a workspace accepts its name. Use -o json for ids.
_COLUMNS = [
    ("NAME", "name"),
    ("ENV", "environment"),
    ("ACCOUNT", "aws_account_id"),
    ("REGION", "region"),
    ("REF", "repo_ref"),
    ("DIR", "tf_working_dir"),
]


def resolve_workspace(obj: AppCtx, ref: str) -> dict:
    """Accept an id or a name. Names are matched case-insensitively.

    An ambiguous name is an error rather than a silent pick — applying to the
    wrong workspace because two share a name is not a recoverable mistake.
    """
    rows = obj.client.get("/workspaces") or []
    for row in rows:
        if str(row.get("id")) == ref:
            return row
    matches = [r for r in rows if str(r.get("name", "")).lower() == ref.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(str(m.get("id")) for m in matches)
        raise TdtError(
            f"'{ref}' matches {len(matches)} workspaces in this BU ({ids}).",
            ExitCode.USAGE,
            hint="Pass the id instead.",
        )
    raise TdtError(
        f"No workspace '{ref}' in BU '{obj.profile.bu or '(default)'}'.",
        ExitCode.API,
        hint="List them with: tdt ws list",
    )


@app.command("list")
def ws_list(
    ctx: typer.Context,
    env: str | None = typer.Option(None, "--env", help="Filter by environment (client-side)."),
    tag: list[str] | None = typer.Option(
        None, "--tag", "-t",
        help="Filter by tag, repeatable and AND-ed: -t team=payments -t tier=prod. "
             "A bare key (-t owner) matches any value.",
    ),
) -> None:
    """List workspaces in the current BU."""
    obj: AppCtx = ctx.obj
    params = {"tag": list(tag)} if tag else None
    rows = obj.client.get("/workspaces", params=params) or []
    if env:
        rows = [r for r in rows if str(r.get("environment", "")).lower() == env.lower()]
    render(obj.fmt, rows, _COLUMNS, empty="No workspaces in this BU.")


@app.command("tags")
def ws_tags(ctx: typer.Context) -> None:
    """Every tag key in this BU, with values and usage counts."""
    obj: AppCtx = ctx.obj
    keys = obj.client.get("/workspaces/tags") or []
    if obj.fmt is Fmt.json:
        render(obj.fmt, keys)
        return
    rows = [
        {
            "key": k["key"],
            "used": k["count"],
            "values": ", ".join(f"{v['value']} ({v['count']})" for v in k["values"][:6])
            + (" …" if len(k["values"]) > 6 else ""),
        }
        for k in keys
    ]
    render(obj.fmt, rows, [("KEY", "key"), ("USED", "used"), ("VALUES", "values")],
           empty="No tags in this BU.")


@app.command("tag")
def ws_tag(
    ctx: typer.Context,
    workspaces: list[str] = typer.Argument(..., help="Workspace ids or names."),
    set_: list[str] | None = typer.Option(
        None, "--set", "-s", help="key=value, repeatable. Merges — other tags are kept.",
    ),
    unset: list[str] | None = typer.Option(
        None, "--unset", "-u", help="Tag key to remove, repeatable.",
    ),
) -> None:
    """Set or remove tags across one or more workspaces.

    `--set` merges per key, so tagging a batch's `team` never wipes the `owner`
    tag one of them happens to carry. The whole batch is rejected if any
    workspace is outside this BU — a half-applied bulk edit is worse than none.
    """
    obj: AppCtx = ctx.obj
    if not set_ and not unset:
        raise TdtError("Give at least one --set or --unset.", ExitCode.USAGE)

    pairs: dict[str, str] = {}
    for item in set_ or []:
        if "=" not in item:
            raise TdtError(f"--set expects key=value, got '{item}'.", ExitCode.USAGE)
        key, _, value = item.partition("=")
        pairs[key.strip()] = value.strip()

    ids = [resolve_workspace(obj, ref)["id"] for ref in workspaces]
    result = obj.client.post(
        "/workspaces/tags",
        {"workspace_ids": ids, "set": pairs, "unset": list(unset or [])},
    )
    if obj.fmt is Fmt.json:
        render(obj.fmt, result)
        return
    ok(f"Updated {result['updated']} workspace(s).")
    render(
        obj.fmt,
        [{"name": w["name"], "tags": ", ".join(f"{k}={v}" for k, v in sorted((w["tags"] or {}).items())) or "—"}
         for w in result["workspaces"]],
        [("NAME", "name"), ("TAGS", "tags")],
    )


@app.command("get")
def ws_get(ctx: typer.Context, workspace: str = typer.Argument(..., help="Workspace id or name.")) -> None:
    """Show one workspace's full detail."""
    obj: AppCtx = ctx.obj
    ws = resolve_workspace(obj, workspace)
    render(obj.fmt, obj.client.get(f"/workspaces/{ws['id']}"))


@app.command("create")
def ws_create(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name"),
    environment: str = typer.Option(..., "--env"),
    aws_account_id: str = typer.Option(..., "--account"),
    region: str = typer.Option(..., "--region"),
    tf_working_dir: str = typer.Option(..., "--dir", help="Leaf path inside the repo."),
    repo_url: str = typer.Option(..., "--repo"),
    repo_ref: str = typer.Option("main", "--ref"),
) -> None:
    """Create a workspace. TDT does not create the repo leaf — it must exist."""
    obj: AppCtx = ctx.obj
    body = {
        "name": name,
        "environment": environment,
        "aws_account_id": aws_account_id,
        "region": region,
        "tf_working_dir": tf_working_dir,
        "repo_url": repo_url,
        "repo_ref": repo_ref,
    }
    created = obj.client.post("/workspaces", body)
    if obj.fmt is Fmt.json:
        render(obj.fmt, created)
    else:
        ok(f"Created workspace {created.get('name')} ({created.get('id')}).")


@app.command("set-ref")
def ws_set_ref(
    ctx: typer.Context,
    workspace: str = typer.Argument(...),
    ref: str = typer.Argument(..., help="Branch or tag to plan/apply from."),
) -> None:
    """Pin the workspace to a git ref — how you plan a branch pre-merge."""
    obj: AppCtx = ctx.obj
    ws = resolve_workspace(obj, workspace)
    updated = obj.client.put(f"/workspaces/{ws['id']}", {"repo_ref": ref})
    if obj.fmt is Fmt.json:
        render(obj.fmt, updated)
    else:
        ok(f"{ws['name']} now tracks '{ref}'.")


@app.command("branches")
def ws_branches(ctx: typer.Context, workspace: str = typer.Argument(...)) -> None:
    """List branches available on the workspace's repo."""
    obj: AppCtx = ctx.obj
    ws = resolve_workspace(obj, workspace)
    data = obj.client.get(f"/workspaces/{ws['id']}/branches")
    rows = data if isinstance(data, list) else data.get("branches", [])
    normalized = [{"branch": b} if isinstance(b, str) else b for b in rows]
    render(obj.fmt, normalized, [("BRANCH", "branch")] if normalized and "branch" in normalized[0] else None)


@app.command("sync")
def ws_sync(
    ctx: typer.Context,
    workspace: str | None = typer.Argument(None, help="Omit to sync every workspace in the BU."),
) -> None:
    """Re-read the repo to refresh path status / discovered leaves."""
    obj: AppCtx = ctx.obj
    if workspace is None:
        render(obj.fmt, obj.client.post("/workspaces/sync"))
        return
    ws = resolve_workspace(obj, workspace)
    render(obj.fmt, obj.client.post(f"/workspaces/{ws['id']}/sync"))


@app.command("discover")
def ws_discover(
    ctx: typer.Context,
    repo_url: str = typer.Option(..., "--repo"),
    ref: str = typer.Option("main", "--ref"),
) -> None:
    """Walk a repo for importable terraform leaves."""
    obj: AppCtx = ctx.obj
    result = obj.client.post("/workspaces/discover", {"repo_url": repo_url, "repo_ref": ref})
    if obj.fmt is Fmt.json:
        render(obj.fmt, result)
        return
    leaves = result.get("leaves") or result.get("candidates") or []
    render(obj.fmt, leaves, empty="No importable leaves found.")


@app.command("unlock")
def ws_unlock(ctx: typer.Context, workspace: str = typer.Argument(...)) -> None:
    """Release a stuck state lock (advisory lock, not DynamoDB)."""
    obj: AppCtx = ctx.obj
    ws = resolve_workspace(obj, workspace)
    status = obj.client.get(f"/workspaces/{ws['id']}/state-lock")
    if obj.fmt is Fmt.table:
        echo(f"[dim]Current lock: {status}[/dim]")
    obj.client.delete(f"/workspaces/{ws['id']}/state-lock")
    ok(f"Released state lock on {ws['name']}.")


@app.command("delete")
def ws_delete(
    ctx: typer.Context,
    workspace: str = typer.Argument(...),
    force: bool = typer.Option(
        False, "--force", help="Required for git-synced workspaces (Untrack / Force-delete)."
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Stop tracking a workspace. Never runs `terraform destroy`.

    This drops TDT's row (and children) and leaves the real infrastructure and
    the tfstate untouched. A plain delete of a git-synced workspace 409s on
    purpose; `--force` is the deliberate override.
    """
    obj: AppCtx = ctx.obj
    ws = resolve_workspace(obj, workspace)
    if not yes:
        typer.confirm(
            f"Stop tracking '{ws['name']}'? Infra and tfstate are left intact.",
            abort=True,
        )
    obj.client.delete(
        f"/workspaces/{ws['id']}",
        params={"force": str(force).lower(), "delete_state": "false"},
    )
    ok(f"Untracked {ws['name']}. Infra and state were not touched.")
