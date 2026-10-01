"""Guard: every endpoint the VS Code extension calls must still exist in this
API. Same idea as test_cli_api_contract.py — `services/vscode/api_contract.json`
names every path the extension depends on; if a router moves or renames one,
this test names the extension feature that just broke."""
import json
from pathlib import Path

import pytest

CONTRACT = Path(__file__).resolve().parents[2] / "vscode" / "api_contract.json"
PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def openapi() -> dict:
    from app.main import app

    return app.openapi()


@pytest.fixture(scope="module")
def contract() -> list[dict]:
    assert CONTRACT.exists(), f"VS Code contract missing at {CONTRACT}"
    return json.loads(CONTRACT.read_text())["endpoints"]


def test_contract_file_is_non_trivial(contract):
    assert len(contract) >= 15, "the contract looks truncated"


def test_every_extension_endpoint_exists_in_the_api(openapi, contract):
    paths = openapi["paths"]
    missing = []
    for entry in contract:
        full = PREFIX + entry["path"]
        methods = {m.lower() for m in (paths.get(full) or {})}
        if entry["method"].lower() not in methods:
            missing.append(f"{entry['method']} {full} (used by: {entry.get('used_by', '?')})")
    assert not missing, "extension depends on endpoints the API no longer serves:\n  " + "\n  ".join(missing)


def test_contract_entries_are_well_formed(contract):
    for e in contract:
        assert e["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE"}, e
        assert e["path"].startswith("/") and not e["path"].startswith(PREFIX), e
        assert e.get("used_by"), f"{e['method']} {e['path']} has no used_by"
