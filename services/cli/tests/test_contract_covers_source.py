"""Guard: every API path the CLI source actually calls must be declared.

Pairs with `services/api/tests/test_cli_api_contract.py`. That one asks "does the
API still have what we declared?"; this one asks "did we declare everything we
call?". Without both, a new undeclared endpoint would slip past the contract and
be free to rot again.
"""
import ast
import json
import re
from pathlib import Path

import pytest

CLI_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = CLI_ROOT / "api_contract.json"
SOURCE_DIR = CLI_ROOT / "tdt"

# The client methods whose first positional argument is an API path.
_PATH_METHODS = {"get", "post", "put", "patch", "delete", "request"}


def _normalize(path: str) -> str:
    """`/runs/{run_id}/steps` and `f"/runs/{run_id}/steps"` both → `/runs/{}/steps`."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _literal_path(node: ast.AST) -> str | None:
    """Recover the path from a plain string or an f-string with interpolations."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if node.value.startswith("/") else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{}")
        joined = "".join(parts)
        # The auth calls build a full URL — f"{profile.url}/auth/refresh" — so
        # drop a leading base-URL interpolation before matching the path.
        if joined.startswith("{}/"):
            joined = joined[2:]
        return joined if joined.startswith("/") else None
    return None


def _called_paths() -> dict[str, set[str]]:
    """Map normalized path → the set of HTTP methods the source calls on it."""
    found: dict[str, set[str]] = {}
    for py in sorted(SOURCE_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            attr = node.func.attr
            if attr not in _PATH_METHODS or not node.args:
                continue
            if attr == "request":
                # request(method, path, ...) — method is a literal in our code.
                if len(node.args) < 2:
                    continue
                method_node, path_node = node.args[0], node.args[1]
                method = (
                    method_node.value
                    if isinstance(method_node, ast.Constant)
                    else None
                )
                path = _literal_path(path_node)
                if path and method:
                    found.setdefault(_normalize(path), set()).add(method.upper())
                continue
            path = _literal_path(node.args[0])
            if path:
                found.setdefault(_normalize(path), set()).add(attr.upper())
    return found


def _entries() -> list[dict]:
    return json.loads(CONTRACT.read_text())["endpoints"]


@pytest.fixture(scope="module")
def declared() -> dict[str, set[str]]:
    """Every declared endpoint, keyed by normalized path."""
    out: dict[str, set[str]] = {}
    for e in _entries():
        out.setdefault(_normalize(e["path"]), set()).add(e["method"].upper())
    return out


@pytest.fixture(scope="module")
def fetched() -> dict[str, set[str]]:
    """Declared endpoints the CLI actually *calls* — excludes browser hand-offs.

    `/auth/oidc/login` is opened in the user's browser by the --sso flow, so it
    never appears as an httpx call. It is still contract-guarded on the API side.
    """
    out: dict[str, set[str]] = {}
    for e in _entries():
        if e.get("browser"):
            continue
        out.setdefault(_normalize(e["path"]), set()).add(e["method"].upper())
    return out


def test_source_calls_were_actually_detected():
    """Sanity-check the AST walk itself, so a silent zero can't pass this file."""
    called = _called_paths()
    assert "/workspaces" in called
    assert "/runs/{}/graph" in called
    assert len(called) >= 15, f"only found {len(called)} paths — parser probably broke"


def test_every_called_path_is_declared_in_the_contract(declared):
    undeclared = []
    for path, methods in sorted(_called_paths().items()):
        for method in sorted(methods):
            if method not in declared.get(path, set()):
                undeclared.append(f"{method} {path}")
    assert not undeclared, (
        "These calls exist in the CLI but not in api_contract.json:\n  "
        + "\n  ".join(undeclared)
        + "\nAdd them so the API-side contract test can guard them."
    )


def test_contract_has_no_dead_entries(fetched):
    """A declared endpoint nothing calls is stale documentation — drop it."""
    called = _called_paths()
    dead = [
        f"{method} {path}"
        for path, methods in sorted(fetched.items())
        for method in sorted(methods)
        if method not in called.get(path, set())
    ]
    assert not dead, "Declared but never called by the CLI:\n  " + "\n  ".join(dead)


def test_browser_handoff_urls_are_built_somewhere_in_the_source():
    """A `browser: true` entry is exempt from the call check, not from existing.

    Guard against the flag becoming a way to park dead entries: the path must
    still appear literally in the CLI source.
    """
    sources = "\n".join(py.read_text() for py in SOURCE_DIR.rglob("*.py"))
    for e in _entries():
        if e.get("browser"):
            assert e["path"] in sources, (
                f"{e['path']} is marked browser:true but appears nowhere in the CLI source"
            )
