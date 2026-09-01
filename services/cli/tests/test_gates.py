"""The approval gate: the reason `tdt run apply` is safer than a curl loop.

`--max-destroy 0` has to be a *guarantee*, not a suggestion, so these cases pin
the arithmetic directly rather than going through the polling loop.
"""
import pytest

from tdt.commands.run_cmd import _check_gates, _fmt_summary


def gates(**kw):
    base = dict(
        require_noop=False, max_add=None, max_change=None,
        max_destroy=None, max_replace=None,
    )
    return {**base, **kw}


def test_no_gates_never_refuses():
    summary = {"add": 5, "change": 3, "destroy": 9, "replace": 2}
    assert _check_gates(summary, **gates()) == []


def test_max_destroy_zero_refuses_any_destroy():
    violations = _check_gates({"add": 1, "destroy": 1}, **gates(max_destroy=0))
    assert len(violations) == 1
    assert "--max-destroy=0" in violations[0]
    assert "destroys 1" in violations[0]


def test_max_destroy_zero_allows_pure_creates():
    assert _check_gates({"add": 12, "change": 0, "destroy": 0}, **gates(max_destroy=0)) == []


def test_max_destroy_is_a_ceiling_not_an_equality():
    assert _check_gates({"destroy": 2}, **gates(max_destroy=3)) == []
    assert _check_gates({"destroy": 3}, **gates(max_destroy=3)) == []
    assert _check_gates({"destroy": 4}, **gates(max_destroy=3)) != []


def test_require_noop_refuses_any_change():
    for field in ("add", "change", "destroy", "replace"):
        violations = _check_gates({field: 1}, **gates(require_noop=True))
        assert violations, f"{field}=1 should violate --require-noop"


def test_require_noop_accepts_a_zero_plan():
    summary = {"add": 0, "change": 0, "destroy": 0, "replace": 0, "no_op": 40}
    assert _check_gates(summary, **gates(require_noop=True)) == []


def test_missing_summary_keys_count_as_zero():
    """A helm diff may not report every key — absence must not read as a breach."""
    assert _check_gates({}, **gates(require_noop=True, max_destroy=0)) == []


def test_none_values_are_treated_as_zero():
    assert _check_gates({"destroy": None}, **gates(max_destroy=0)) == []


def test_all_violated_gates_are_reported_together():
    summary = {"add": 5, "change": 5, "destroy": 5, "replace": 5}
    violations = _check_gates(
        summary, **gates(max_add=0, max_change=0, max_destroy=0, max_replace=0)
    )
    assert len(violations) == 4


def test_replace_is_gated_separately_from_destroy():
    """A replace destroys and recreates — --max-destroy alone must not cover it."""
    assert _check_gates({"replace": 3, "destroy": 0}, **gates(max_destroy=0)) == []
    assert _check_gates({"replace": 3, "destroy": 0}, **gates(max_replace=0)) != []


def test_summary_formatting_is_stable():
    assert _fmt_summary({"add": 1, "change": 2, "destroy": 3, "replace": 4}) == "+1 ~2 -3 ±4"
    assert _fmt_summary({}) == "+0 ~0 -0 ±0"


# ─── destroy confirmation ──────────────────────────────────────────────────


def test_destroy_is_registered_as_a_command():
    """The API has always accepted command=destroy; the CLI omitted it, which
    made a full create→apply→destroy cycle impossible through the CLI alone."""
    import typer.main

    from tdt.cli import app

    group = typer.main.get_command(app)
    run_group = group.commands["run"]
    assert "destroy" in run_group.commands


def test_destroy_declares_the_safety_flags():
    import typer.main

    from tdt.cli import app

    destroy = typer.main.get_command(app).commands["run"].commands["destroy"]
    opts = {o for p in destroy.params for o in (p.opts or [])}
    assert "--yes" in opts, "needs an explicit confirmation bypass for automation"
    assert "--max-destroy" in opts, "needs a ceiling so an oversized plan is refused"
    assert "--auto-approve" in opts
