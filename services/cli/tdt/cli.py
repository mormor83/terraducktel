"""`tdt` entrypoint: global options, command wiring, and exit-code mapping.

Every command reaches the API through `AppCtx`, so `--profile` / `--url` / `--bu`
/ `-o` are resolved once here rather than re-parsed per command.
"""
from __future__ import annotations

import os
import sys

import typer

from . import __version__
from .argv import GLOBAL_OPTS, hoist_global_options
from .commands import auth_cmd, misc_cmd, run_cmd, ws_cmd
from .errors import ExitCode, TdtError
from .output import Fmt, echo, fail
from .state import AppCtx

app = typer.Typer(
    no_args_is_help=True,
    add_completion=True,
    help=(
        "Terraducktel CLI — drive workspaces and runs from the terminal.\n\n"
        "Exit codes: 0 ok · 1 usage · 2 auth · 3 plan failed · 4 rejected/gated "
        "· 5 apply failed · 6 timeout · 7 API error."
    ),
)

app.add_typer(ws_cmd.app, name="ws")
app.add_typer(run_cmd.app, name="run")
app.add_typer(auth_cmd.app, name="profile")
app.add_typer(misc_cmd.drift_app, name="drift")
app.add_typer(misc_cmd.show_app, name="show")
app.add_typer(misc_cmd.skill_app, name="skill")

# Top-level verbs live outside their Typer groups so they read as `tdt login`
# rather than `tdt profile login`.
app.command("login")(auth_cmd.login)
app.command("logout")(auth_cmd.logout)
app.command("whoami")(auth_cmd.whoami)
app.command("context")(misc_cmd.context)


def _version_callback(value: bool) -> None:
    if value:
        echo(f"tdt {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Named profile from ~/.config/tdt/config.toml.",
    ),
    url: str | None = typer.Option(None, "--url", help="Override the API base URL."),
    bu: str | None = typer.Option(
        None, "--bu", help="Business-unit slug (X-Business-Unit). Ignored for API keys.",
    ),
    output: Fmt = typer.Option(Fmt.table, "--output", "-o", help="Output format."),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Print version and exit.",
    ),
) -> None:
    ctx.obj = AppCtx(profile_name=profile, url=url, bu=bu, fmt=output)


def _click_exceptions() -> tuple[type[BaseException], type[BaseException], type[BaseException]]:
    """Resolve (ClickException, Abort, NoArgsIsHelpError) from the click Typer uses.

    Typer >= 0.27 **vendors** click as `typer._click`, and there may be no
    top-level `click` installed at all. Even when there is, the vendored
    exceptions are different classes, so `except click.UsageError` would never
    match. Always prefer Typer's own copy; fall back to real click for older
    Typer versions that depend on it.
    """
    module = None
    for name in ("typer._click.exceptions", "click.exceptions"):
        try:
            module = __import__(name, fromlist=["*"])
            break
        except ModuleNotFoundError:
            continue
    if module is None:  # pragma: no cover — Typer cannot be installed without one
        return (SystemExit, SystemExit, SystemExit)

    click_exc = getattr(module, "ClickException", SystemExit)
    abort = getattr(module, "Abort", SystemExit)
    # Added in click 8.x; fall back to the base so the except clause stays valid.
    no_args = getattr(module, "NoArgsIsHelpError", click_exc)
    return (click_exc, abort, no_args)


_CLICK_EXCEPTION, _CLICK_ABORT, _CLICK_NO_ARGS_IS_HELP = _click_exceptions()


def _explain_global_option(exc: BaseException) -> None:
    """If an unknown option is really a global one, say where it belongs.

    The hoist in tdt/argv.py handles this automatically in the normal case; this
    covers the leftover where a subcommand declares the same option name, so the
    hoist correctly declined and Click still rejected it.
    """
    name = getattr(exc, "option_name", None)
    if name in GLOBAL_OPTS:
        echo(
            f"\n[dim]{name} is a global option — it goes before the command:[/dim]\n"
            f"[dim]  tdt {name} <value> <command>[/dim]"
        )


def main() -> None:
    # Accept the global options after the command name too — see tdt/argv.py.
    try:
        import typer.main as _typer_main

        argv = hoist_global_options(sys.argv[1:], _typer_main.get_command(app))
    except Exception:  # noqa: BLE001 — a shim must never break invocation
        argv = sys.argv[1:]

    try:
        app(args=argv, standalone_mode=False)
    except TdtError as exc:
        fail(exc.message, exc.hint)
        sys.exit(exc.code)
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
    except _CLICK_NO_ARGS_IS_HELP:
        # `tdt` / `tdt ws` with no subcommand: help was already printed, and
        # asking for help is not an error.
        sys.exit(ExitCode.OK)
    except _CLICK_ABORT:
        fail("Aborted.")
        sys.exit(ExitCode.USAGE)
    except _CLICK_EXCEPTION as exc:
        # Covers UsageError / BadParameter / NoSuchOption. Click formats these
        # better than str() does (it includes usage lines and suggestions).
        show = getattr(exc, "show", None)
        if callable(show):
            show()
        else:  # pragma: no cover
            fail(str(exc))
        _explain_global_option(exc)
        sys.exit(ExitCode.USAGE)
    except KeyboardInterrupt:
        fail("Interrupted.")
        # The run keeps going server-side; say so rather than implying a rollback.
        echo("[dim]Any in-flight run is still executing — check `tdt run list`.[/dim]")
        sys.exit(ExitCode.USAGE)
    except Exception as exc:  # noqa: BLE001 — last resort, see below
        # A user-facing entrypoint must never print a traceback: this catches
        # anything the handlers above missed, including a future Typer release
        # moving the vendored click exceptions out from under `_click_exceptions`.
        # `TDT_DEBUG=1` re-raises so the traceback is still one env var away.
        if os.environ.get("TDT_DEBUG"):
            raise
        fail(
            f"Unexpected {type(exc).__name__}: {exc}",
            hint="Re-run with TDT_DEBUG=1 for the full traceback.",
        )
        sys.exit(ExitCode.API)
    sys.exit(ExitCode.OK)


if __name__ == "__main__":
    main()
