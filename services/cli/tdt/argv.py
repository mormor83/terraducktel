"""Let the global options be typed anywhere, not only before the command.

Click binds an option to the command that declares it, so `--url` on the root
callback is invisible to `tdt whoami --url X` — you get "No such option: --url",
which is a dead end for anyone who types the way most CLIs accept.

This shim moves the global options to the front of argv before Click parses it,
but **only when the target subcommand does not declare that option itself**.
That exception is load-bearing: `tdt profile add p --url X --bu home` has its own
`--url`/`--bu` and must keep them.

Everything after a bare `--` is left untouched.
"""
from __future__ import annotations

from typing import Iterable

# Global options from the root callback. Values may be attached (`--url=X`) or
# separate (`--url X`); the flags take no value.
GLOBAL_VALUE_OPTS = {"--profile", "-p", "--url", "--bu", "--output", "-o"}
GLOBAL_FLAG_OPTS = {"--version"}
GLOBAL_OPTS = GLOBAL_VALUE_OPTS | GLOBAL_FLAG_OPTS


def _declared_opts(command) -> set[str]:
    """Every option string the given click command declares."""
    out: set[str] = set()
    for param in getattr(command, "params", []) or []:
        out.update(getattr(param, "opts", None) or [])
        out.update(getattr(param, "secondary_opts", None) or [])
    return out


def _resolve_target(group, tokens: Iterable[str]):
    """Walk the command tree over the positional tokens to find the target command.

    Descends only into names that really are subcommands, and stops at the first
    token that isn't one — so an option *value* that happens to look like a
    command name can't drag the walk somewhere wrong.
    """
    command = group
    for token in tokens:
        if token.startswith("-"):
            continue
        commands = getattr(command, "commands", None)
        if not commands:
            break
        sub = commands.get(token)
        if sub is None:
            break
        command = sub
    return command


def hoist_global_options(argv: list[str], group) -> list[str]:
    """Return argv with non-conflicting global options moved to the front."""
    if not argv:
        return argv

    # Split off anything after `--`; it is positional data, never ours to touch.
    if "--" in argv:
        cut = argv.index("--")
        head, tail = argv[:cut], argv[cut:]
    else:
        head, tail = list(argv), []

    target = _resolve_target(group, head)
    # The root group declares the globals too; only a *subcommand* claiming the
    # same name should block the hoist.
    conflicts = _declared_opts(target) if target is not group else set()

    hoisted: list[str] = []
    kept: list[str] = []
    i = 0
    seen_command = False
    while i < len(head):
        token = head[i]
        name, _, attached = token.partition("=")

        if not token.startswith("-"):
            seen_command = True
            kept.append(token)
            i += 1
            continue

        # Options before the command name are already in the right place.
        if seen_command and name in GLOBAL_OPTS and name not in conflicts:
            if name in GLOBAL_FLAG_OPTS or attached:
                hoisted.append(token)
                i += 1
            elif i + 1 < len(head):
                hoisted.extend([token, head[i + 1]])
                i += 2
            else:
                # Trailing value-less option: leave it for Click to complain about.
                kept.append(token)
                i += 1
            continue

        kept.append(token)
        i += 1

    return hoisted + kept + tail
