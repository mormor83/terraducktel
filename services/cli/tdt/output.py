"""Rendering: `-o table` for humans, `-o json` for machines.

The default is `table` regardless of whether stdout is a TTY. Auto-switching on
`isatty()` would mean a script's output silently changes shape depending on how
it was invoked; being explicit is worth the extra six characters.
"""
from __future__ import annotations

import json
import sys
from enum import Enum
from typing import Any, Iterable, Sequence

from rich.console import Console
from rich.table import Table

_console = Console()
_err = Console(stderr=True)


class Fmt(str, Enum):
    table = "table"
    json = "json"


def echo(msg: str = "") -> None:
    _console.print(msg, highlight=False)


def warn(msg: str) -> None:
    _err.print(f"[yellow]![/yellow] {msg}", highlight=False)


def fail(msg: str, hint: str | None = None) -> None:
    _err.print(f"[red]✗[/red] {msg}", highlight=False)
    if hint:
        for line in hint.splitlines():
            _err.print(f"  [dim]{line}[/dim]", highlight=False)


def ok(msg: str) -> None:
    _console.print(f"[green]✓[/green] {msg}", highlight=False)


def dump_json(data: Any) -> None:
    # Straight to stdout, not through rich — rich would wrap and colourise, and
    # this output exists to be piped into jq.
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")


def short_ts(value: Any) -> str | None:
    """`2026-07-08T13:20:04.817563Z` → `2026-07-08 13:20`, for table columns."""
    if not value:
        return None
    text = str(value)
    return text[:16].replace("T", " ") if len(text) >= 16 else text


def age(value: Any) -> str | None:
    """ISO timestamp → compact age (`4m`, `3h`, `8d`, `6w`).

    A run list is read as "what happened recently", so age beats an absolute
    timestamp — and it fits in seven columns instead of sixteen.
    """
    if not value:
        return None
    from datetime import datetime, timezone

    text = str(value).replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return short_ts(value)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - when).total_seconds()
    if secs < 0:
        return "now"
    for cutoff, div, unit in (
        (90, 1, "s"), (5400, 60, "m"), (172800, 3600, "h"),
        (1209600, 86400, "d"), (float("inf"), 604800, "w"),
    ):
        if secs < cutoff:
            return f"{int(secs // div)}{unit}"
    return short_ts(value)


def _cell(value: Any) -> str:
    if value is None:
        return "[dim]—[/dim]"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


_STATUS_STYLE = {
    "applied": "green",
    "planned": "cyan",
    "success": "green",
    "running": "yellow",
    "planning": "yellow",
    "applying": "yellow",
    "pending": "dim",
    "skipped": "dim",
    "awaiting_approval": "magenta",
    "failed": "red",
    "cancelled": "red",
    "drifted": "red",
    "in_sync": "green",
    "orphaned": "red",
}


def _style_status(value: Any) -> str:
    style = _STATUS_STYLE.get(str(value))
    return f"[{style}]{value}[/{style}]" if style else _cell(value)


def render(
    fmt: Fmt,
    rows: Sequence[dict] | dict | None,
    columns: Iterable[tuple[str, str]] | None = None,
    *,
    empty: str = "No results.",
    title: str | None = None,
) -> None:
    """Print `rows` as JSON, or as a table of `columns` = [(header, key), …].

    JSON always emits the API payload untouched, so `-o json` is a faithful view
    of what the server said and stays stable as table columns get tuned.
    """
    if fmt is Fmt.json:
        dump_json(rows if rows is not None else [])
        return

    if rows is None or (isinstance(rows, list) and not rows):
        echo(f"[dim]{empty}[/dim]")
        return

    if isinstance(rows, dict):
        table = Table(show_header=False, box=None, pad_edge=False, title=title)
        table.add_column(style="dim")
        table.add_column()
        for key, value in rows.items():
            table.add_row(
                key, _style_status(value) if "status" in key else _cell(value)
            )
        _console.print(table)
        return

    cols = list(columns or [(k, k) for k in rows[0]])
    table = Table(box=None, pad_edge=False, header_style="bold", title=title)
    for header, key in cols:
        # Ids are copy-pasted into the next command, so never fold them mid-uuid;
        # free-text columns (paths, names) wrap instead of being truncated.
        is_id = header == "ID" or key.endswith("id")
        table.add_column(header, overflow="ellipsis" if is_id else "fold", no_wrap=is_id)
    for row in rows:
        table.add_row(*[
            _style_status(row.get(key)) if "status" in key else _cell(row.get(key))
            for _, key in cols
        ])
    _console.print(table)
