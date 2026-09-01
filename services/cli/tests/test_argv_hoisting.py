"""Global options must work on either side of the command name.

Reported as "not very intuitive": `tdt whoami --url https://…` died with
"No such option: --url", because Click binds an option to the command that
declares it and the globals live on the root callback.

The exception that makes this subtle: `tdt profile add p --url X` has its *own*
--url and must keep it. Hoisting is therefore conditional on the target
subcommand not declaring the same option.
"""
import pytest
import typer.main

from tdt.argv import hoist_global_options
from tdt.cli import app


@pytest.fixture(scope="module")
def group():
    return typer.main.get_command(app)


def h(argv, group):
    return hoist_global_options(argv, group)


# ─── the reported failure ──────────────────────────────────────────────────


def test_url_after_the_command_is_hoisted(group):
    assert h(["whoami", "--url", "https://x"], group) == ["--url", "https://x", "whoami"]


def test_attached_value_form_is_hoisted(group):
    assert h(["whoami", "--url=https://x"], group) == ["--url=https://x", "whoami"]


def test_short_output_flag_is_hoisted(group):
    assert h(["profile", "list", "-o", "json"], group) == ["-o", "json", "profile", "list"]


def test_multiple_globals_are_all_hoisted(group):
    out = h(["ws", "list", "--bu", "other", "-o", "json"], group)
    assert out[-2:] == ["ws", "list"]
    assert set(out[:-2]) == {"--bu", "other", "-o", "json"}


def test_globals_already_in_front_are_left_alone(group):
    argv = ["--url", "https://x", "whoami"]
    assert h(argv, group) == argv


# ─── the conflict exception ────────────────────────────────────────────────


def test_profile_add_keeps_its_own_url_and_bu(group):
    """The command declares these itself — hoisting would steal them."""
    argv = ["profile", "add", "p", "--url", "https://x", "--bu", "home"]
    assert h(argv, group) == argv


def test_global_output_still_hoists_past_profile_add(group):
    """profile add declares --url/--bu but not --output, so -o may still move."""
    out = h(["profile", "add", "p", "--url", "https://x", "-o", "json"], group)
    assert out[:2] == ["-o", "json"]
    assert "--url" in out and out[out.index("--url") + 1] == "https://x"


# ─── things that must not be disturbed ─────────────────────────────────────


def test_subcommand_options_are_untouched(group):
    argv = ["run", "list", "--status", "failed", "--limit", "5"]
    assert h(argv, group) == argv


def test_run_steps_logs_flag_does_not_collide_with_global_output(group):
    """`--logs` was renamed from `--output` precisely to avoid this."""
    argv = ["run", "steps", "abc", "--logs"]
    assert h(argv, group) == argv


def test_everything_after_a_double_dash_is_preserved(group):
    argv = ["ws", "get", "--", "--url", "not-an-option"]
    assert h(argv, group) == argv


def test_empty_argv_is_returned_unchanged(group):
    assert h([], group) == []


def test_bare_command_is_unchanged(group):
    assert h(["whoami"], group) == ["whoami"]


def test_trailing_valueless_global_is_left_for_click_to_report(group):
    """`tdt whoami --url` with no value must still produce Click's usage error."""
    assert h(["whoami", "--url"], group) == ["whoami", "--url"]


def test_an_option_value_that_looks_like_a_command_does_not_move_the_target(group):
    """`--status run` must not make the walk descend into the `run` group."""
    argv = ["run", "list", "--status", "planned"]
    assert h(argv, group) == argv


def test_unknown_command_is_passed_through_for_click_to_reject(group):
    argv = ["nonsuch", "--url", "https://x"]
    out = h(argv, group)
    assert "nonsuch" in out
