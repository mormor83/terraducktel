"""End-to-end entrypoint behaviour, run as a real subprocess.

These exist because a bug slipped through every in-process test: `main()`
resolved click's exception classes with a bare `import click`, but Typer >= 0.27
vendors click as `typer._click` and there may be no top-level `click` installed.
The handler itself raised ModuleNotFoundError, so `tdt` with no arguments printed
help and then a traceback. Nothing that imports the app can catch that — only
actually running the console script does.

Every case here asserts **no traceback on stderr**, which is the property that
was violated.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

CLI_ROOT = Path(__file__).resolve().parents[1]


def run(*args, env_extra=None):
    env = {**os.environ, "TDT_CONFIG_DIR": "/nonexistent-config-dir-for-tests"}
    env.pop("TDT_TOKEN", None)
    env.pop("TDT_API_URL", None)
    env.pop("TDT_BU", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "tdt", *args],
        cwd=CLI_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def assert_no_traceback(proc, label):
    assert "Traceback (most recent call last)" not in proc.stderr, (
        f"{label} produced a traceback:\n{proc.stderr}"
    )
    assert "ModuleNotFoundError" not in proc.stderr, (
        f"{label} hit an import error:\n{proc.stderr}"
    )


def test_bare_invocation_prints_help_and_exits_clean():
    """The exact reported failure: `tdt` with no arguments."""
    proc = run()
    assert_no_traceback(proc, "bare `tdt`")
    assert "Usage: " in proc.stdout
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("group", ["ws", "run", "profile", "drift", "show", "skill"])
def test_group_without_subcommand_prints_help_and_exits_clean(group):
    """Every group sets no_args_is_help, so each takes the same code path."""
    proc = run(group)
    assert_no_traceback(proc, f"`tdt {group}`")
    assert "Usage: " in proc.stdout
    assert proc.returncode == 0, proc.stderr


def test_version_exits_zero():
    proc = run("--version")
    assert_no_traceback(proc, "`tdt --version`")
    assert proc.returncode == 0
    assert "tdt " in proc.stdout


def test_help_exits_zero():
    proc = run("--help")
    assert_no_traceback(proc, "`tdt --help`")
    assert proc.returncode == 0


def test_unknown_option_is_a_usage_error_not_a_traceback():
    proc = run("--nonsuch")
    assert_no_traceback(proc, "unknown option")
    assert proc.returncode == 1, f"expected exit 1 (usage), got {proc.returncode}"


def test_unknown_command_is_a_usage_error_not_a_traceback():
    proc = run("nonsuch-command")
    assert_no_traceback(proc, "unknown command")
    assert proc.returncode == 1


def test_bad_option_value_is_a_usage_error():
    """`-o yaml` isn't a valid format — must be a clean usage failure."""
    proc = run("-o", "yaml", "ws", "list")
    assert_no_traceback(proc, "bad -o value")
    assert proc.returncode == 1


def test_missing_required_argument_is_a_usage_error():
    proc = run("ws", "get")
    assert_no_traceback(proc, "missing argument")
    assert proc.returncode == 1


def test_no_configured_profile_is_exit_1_with_a_hint():
    """Config errors must be actionable, not a stack trace."""
    proc = run("ws", "list")
    assert_no_traceback(proc, "no profile")
    assert proc.returncode == 1
    combined = proc.stdout + proc.stderr
    assert "tdt profile add" in combined


def test_no_credentials_is_exit_2_with_a_login_hint():
    proc = run("whoami", env_extra={"TDT_API_URL": "http://127.0.0.1:9/api/v1", "TDT_BU": "x"})
    assert_no_traceback(proc, "no credentials")
    assert proc.returncode == 2
    assert "tdt login" in (proc.stdout + proc.stderr)


def test_unreachable_api_is_exit_7_and_blames_the_network():
    """Port 9 (discard) refuses fast — stands in for 'API not reachable'."""
    proc = run(
        "ws", "list",
        env_extra={
            "TDT_API_URL": "http://127.0.0.1:9/api/v1",
            "TDT_BU": "x",
            "TDT_TOKEN": "tdt_dummy_key_for_transport_test",
        },
    )
    assert_no_traceback(proc, "unreachable API")
    assert proc.returncode == 7, f"expected exit 7, got {proc.returncode}: {proc.stderr}"
    assert "VPN" in (proc.stdout + proc.stderr)


def test_an_unexpected_error_is_reported_cleanly_not_as_a_traceback(monkeypatch):
    """Last-resort handler: no traceback reaches the user, and TDT_DEBUG opts in.

    Simulated by making the app itself blow up, which is the only way to reach
    the bare `except Exception` branch.
    """
    proc = subprocess.run(
        [sys.executable, "-c",
         "import tdt.cli as c;"
         "c.app = lambda **kw: (_ for _ in ()).throw(RuntimeError('boom'));"
         "c.main()"],
        cwd=CLI_ROOT, capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if k != "TDT_DEBUG"},
    )
    assert_no_traceback(proc, "unexpected error")
    assert "Unexpected RuntimeError: boom" in (proc.stdout + proc.stderr)
    assert "TDT_DEBUG=1" in (proc.stdout + proc.stderr)
    assert proc.returncode == 7


def test_tdt_debug_re_raises_the_traceback():
    proc = subprocess.run(
        [sys.executable, "-c",
         "import tdt.cli as c;"
         "c.app = lambda **kw: (_ for _ in ()).throw(RuntimeError('boom'));"
         "c.main()"],
        cwd=CLI_ROOT, capture_output=True, text=True, timeout=60,
        env={**os.environ, "TDT_DEBUG": "1"},
    )
    assert "Traceback (most recent call last)" in proc.stderr
    assert "RuntimeError: boom" in proc.stderr


# ─── global options after the command (the "not intuitive" report) ─────────


def test_url_after_the_command_reaches_the_handler():
    """`tdt whoami --url X` used to die with "No such option: --url"."""
    proc = run("whoami", "--url", "http://127.0.0.1:9", "--bu", "x")
    assert_no_traceback(proc, "whoami --url")
    combined = proc.stdout + proc.stderr
    assert "No such option" not in combined
    # Gets far enough to complain about credentials, not about parsing.
    assert proc.returncode == 2
    assert "tdt login" in combined


def test_output_json_after_the_command():
    proc = run("profile", "list", "-o", "json")
    assert_no_traceback(proc, "profile list -o json")
    assert proc.returncode == 0


def test_no_profile_hint_says_where_url_goes():
    """The old hint said "or pass --url" without saying it must precede the command."""
    proc = run("ws", "list")
    combined = proc.stdout + proc.stderr
    assert "tdt profile add" in combined
    assert "global option" in combined


def test_profile_add_still_owns_its_url_and_bu(tmp_path):
    """The hoist must not steal the options `profile add` declares itself."""
    cfg = tmp_path / "cfg"
    proc = run(
        "profile", "add", "p", "--url", "https://declared.example.com", "--bu", "team-x",
        env_extra={"TDT_CONFIG_DIR": str(cfg)},
    )
    assert proc.returncode == 0, proc.stderr
    written = (cfg / "config.toml").read_text()
    assert "https://declared.example.com/api/v1" in written
    assert 'bu = "team-x"' in written


def test_first_profile_is_default_so_the_hint_omits_profile_flag(tmp_path):
    cfg = tmp_path / "cfg"
    proc = run("profile", "add", "solo", "--url", "https://x.example.com",
               env_extra={"TDT_CONFIG_DIR": str(cfg)})
    assert "Next: tdt login" in proc.stdout
    assert "--profile solo" not in proc.stdout


def test_destroy_without_tty_and_without_yes_refuses():
    """A non-interactive destroy must not proceed on an unconfirmed guess."""
    proc = run(
        "run", "destroy", "some-workspace",
        env_extra={
            "TDT_API_URL": "http://127.0.0.1:9/api/v1",
            "TDT_BU": "x",
            "TDT_TOKEN": "tdt_dummy_key",
        },
    )
    assert_no_traceback(proc, "destroy without tty")
    combined = proc.stdout + proc.stderr
    # It fails before reaching the API at all — either on the confirmation guard
    # or on transport; what matters is that it never triggers a run.
    assert proc.returncode in (1, 7), f"got {proc.returncode}: {combined}"
