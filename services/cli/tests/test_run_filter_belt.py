"""Client-side re-application of the run-list filters.

Reason it exists: an API build predating the `GET /runs` query params ignores
them and returns every run in the BU. `tdt run list --limit 5` would then print
hundreds of rows while claiming to show five. Re-applying the predicates locally
is a no-op against a server that filtered, and keeps the flags truthful against
one that didn't.
"""
from tdt.commands.run_cmd import _apply_filters_locally


def runs(*specs):
    return [{"id": i, "workspace_id": w, "status": s} for i, w, s in specs]


def test_no_params_is_a_passthrough():
    rows = runs(("1", "a", "applied"), ("2", "b", "failed"))
    assert _apply_filters_locally(rows, {}) == rows


def test_limit_is_enforced_even_if_the_server_ignored_it():
    rows = runs(*[(str(i), "a", "applied") for i in range(50)])
    assert len(_apply_filters_locally(rows, {"limit": 5})) == 5


def test_limit_keeps_the_newest_because_server_order_is_preserved():
    rows = runs(("newest", "a", "applied"), ("mid", "a", "applied"), ("oldest", "a", "applied"))
    assert [r["id"] for r in _apply_filters_locally(rows, {"limit": 2})] == ["newest", "mid"]


def test_workspace_filter_is_enforced():
    rows = runs(("1", "a", "applied"), ("2", "b", "applied"))
    out = _apply_filters_locally(rows, {"workspace_id": "a"})
    assert [r["id"] for r in out] == ["1"]


def test_single_status_filter_is_enforced():
    rows = runs(("1", "a", "applied"), ("2", "a", "failed"))
    out = _apply_filters_locally(rows, {"status": "failed"})
    assert [r["id"] for r in out] == ["2"]


def test_comma_separated_statuses_are_enforced():
    rows = runs(("1", "a", "applied"), ("2", "a", "failed"), ("3", "a", "cancelled"))
    out = _apply_filters_locally(rows, {"status": "failed,cancelled"})
    assert {r["id"] for r in out} == {"2", "3"}


def test_statuses_with_stray_whitespace_are_handled():
    rows = runs(("1", "a", "applied"), ("2", "a", "failed"))
    out = _apply_filters_locally(rows, {"status": " failed , "})
    assert [r["id"] for r in out] == ["2"]


def test_filters_compose_and_limit_applies_last():
    rows = runs(
        ("1", "a", "failed"), ("2", "b", "failed"),
        ("3", "a", "failed"), ("4", "a", "applied"),
    )
    out = _apply_filters_locally(rows, {"workspace_id": "a", "status": "failed", "limit": 1})
    assert [r["id"] for r in out] == ["1"]


def test_is_idempotent_against_an_already_filtered_response():
    """The normal path: the server filtered, so this must change nothing."""
    rows = runs(("1", "a", "failed"))
    params = {"workspace_id": "a", "status": "failed", "limit": 10}
    assert _apply_filters_locally(rows, params) == rows


def test_empty_response_survives():
    assert _apply_filters_locally([], {"limit": 5, "status": "failed"}) == []
