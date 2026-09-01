"""Guard: every endpoint the `tdt` CLI calls must still exist in this API.

The old hand-written SKILL.md drifted — it documented `GET /workspaces/{id}/runs`
(a 405), missed `GET /runs` entirely, and never mentioned `/runs/{id}/cancel`.
Nothing failed when that happened, because prose has no tests. This does:
`services/cli/api_contract.json` names every path the CLI depends on, and if a
router moves or renames one, this test names the CLI command that just broke.
"""
import json
from pathlib import Path

import pytest

CONTRACT = Path(__file__).resolve().parents[2] / "cli" / "api_contract.json"
PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def openapi() -> dict:
    from app.main import app

    return app.openapi()


@pytest.fixture(scope="module")
def contract() -> list[dict]:
    assert CONTRACT.exists(), f"CLI contract missing at {CONTRACT}"
    return json.loads(CONTRACT.read_text())["endpoints"]


def test_contract_file_is_non_trivial(contract):
    assert len(contract) >= 20, "the contract looks truncated"


def test_every_cli_endpoint_exists_in_the_api(openapi, contract):
    paths = openapi["paths"]
    missing = []
    for entry in contract:
        full = PREFIX + entry["path"]
        methods = {m.lower() for m in (paths.get(full) or {})}
        if entry["method"].lower() not in methods:
            missing.append(
                f"{entry['method']} {full} — used by `{entry['used_by']}` "
                f"(API has: {sorted(methods) or 'no such path'})"
            )
    assert not missing, "CLI contract broken:\n  " + "\n  ".join(missing)


def test_cli_never_relies_on_workspace_scoped_run_listing(openapi):
    """`GET /workspaces/{id}/runs` is a 405 — the CLI must use `/runs?workspace_id=`.

    This is the exact stale claim the old skill carried, pinned so it can't
    quietly become true-then-false again without someone noticing.
    """
    entry = openapi["paths"].get(f"{PREFIX}/workspaces/{{workspace_id}}/runs") or {}
    assert "get" not in entry, (
        "A GET on /workspaces/{id}/runs now exists — update the CLI and the "
        "contract to prefer it over /runs?workspace_id=."
    )


def test_run_list_still_accepts_the_cli_filters(openapi):
    params = {
        p["name"]
        for p in openapi["paths"][f"{PREFIX}/runs"]["get"].get("parameters", [])
    }
    assert {"workspace_id", "status", "limit"} <= params, (
        f"`tdt run list` filters missing from GET /runs (has: {sorted(params)})"
    )


def test_steps_still_accepts_the_watch_cursor(openapi):
    params = {
        p["name"]
        for p in openapi["paths"][f"{PREFIX}/runs/{{run_id}}/steps"]["get"].get("parameters", [])
    }
    assert {"since", "include_output"} <= params, (
        f"`tdt run watch` cursor params missing from GET /steps (has: {sorted(params)})"
    )
