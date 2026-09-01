"""`docs/API.md` must document every endpoint the API actually serves.

The reference drifted quietly: by the time anyone looked, `POST /auth/refresh`,
`POST /api-keys/{id}/rotate`, both `/workspaces/tags` routes and the query
params on `GET /runs` and `/runs/{id}/steps` were all live and undocumented,
while the doc still read as if they didn't exist. Nothing failed, because prose
has no tests — the same failure mode `api_contract.json` guards for the CLI.

This closes it from the other side: the OpenAPI schema is generated from the
routers, so it cannot lie about what exists.
"""
import re
from pathlib import Path

import pytest

API_MD = Path(__file__).resolve().parents[3] / "docs" / "API.md"

# `/internal/*` is included on both sides: those routes are state-token-guarded
# rather than client-facing, but API.md documents them, so the reference should
# stay honest about them too.

# Section headings carry the shared prefix, so rows below them use a `…`
# shorthand for it. Resolve those by hand rather than teaching the parser to
# track headings.
SHORTHAND = {
    ("GET", "/workspaces/{}/variables"),
    ("POST", "/workspaces/{}/variables"),
    ("PATCH", "/workspaces/{}/variables/{}"),
    ("DELETE", "/workspaces/{}/variables/{}"),
}


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path.replace("/api/v1", "").rstrip("/")) or "/"


@pytest.fixture(scope="module")
def live() -> set[tuple[str, str]]:
    from app.main import app

    out = set()
    for path, ops in app.openapi()["paths"].items():
        norm = _norm(path)
        for method in ops:
            if method in ("get", "post", "put", "patch", "delete"):
                out.add((method.upper(), norm))
    return out


@pytest.fixture(scope="module")
def documented() -> set[tuple[str, str]]:
    assert API_MD.exists(), f"API reference missing at {API_MD}"
    text = API_MD.read_text()
    found = set()
    # `| GET | `/path` | …` table rows
    for m, p in re.findall(r"^\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`([^`]+)`", text, re.M):
        found.add((m, _norm(p)))
    # `### POST /workspaces/import` headings and `**POST /auth/token**` prose
    for m, p in re.findall(r"(GET|POST|PUT|PATCH|DELETE)\s+`?(/[A-Za-z0-9_\-{}/.]+)", text):
        found.add((m, _norm(p)))
    return found | SHORTHAND


def test_the_parser_found_something(documented):
    """A silent zero here would make the real assertion vacuous."""
    assert len(documented) > 100, f"only parsed {len(documented)} endpoints from API.md"


def test_every_endpoint_is_documented(live, documented):
    missing = sorted(live - documented)
    assert not missing, (
        "These endpoints exist but are absent from docs/API.md:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in missing)
    )


def test_no_endpoint_is_documented_that_no_longer_exists(live, documented):
    """Catches a route renamed or removed without the reference following."""
    # Judge only paths the doc spells out in full. Rows under a section heading
    # use a `…` prefix for the shared path; those are resolved by SHORTHAND.
    concrete = {(m, p) for m, p in documented if "…" not in p}
    stale = sorted(concrete - live - SHORTHAND)
    assert not stale, (
        "docs/API.md documents endpoints the API no longer serves:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in stale)
    )
