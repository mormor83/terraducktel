"""OPA/conftest policy CRUD, version history, BU scoping, RBAC, and the engine.

Covers:
  - Admin-only create/update/delete; viewer is read-only.
  - BU scoping: a member of one BU cannot see another BU's policies.
  - Version history: every edit snapshots; restore creates a new version.
  - The /integrations/opa gate config round-trips.
  - The executor bundle endpoint returns enabled policies for a run's BU.
  - conftest `evaluate` and `verify` (skipped when conftest isn't on PATH).
"""
from __future__ import annotations

import json
import shutil
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("default_bu")

_CONFTEST = shutil.which("conftest")
needs_conftest = pytest.mark.skipif(_CONFTEST is None, reason="conftest not installed")


def _h(token: str, bu: str = "default") -> dict:
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


_REGO_PUBLIC = """package main

deny[msg] {
    resource := input.resource_changes[_]
    resource.type == "aws_s3_bucket_public_access_block"
    resource.change.after.block_public_acls == false
    msg := sprintf("Public access must be blocked for %s", [resource.address])
}
"""

_PLAN_PUBLIC = json.dumps(
    {
        "resource_changes": [
            {
                "address": "aws_s3_bucket_public_access_block.bad",
                "type": "aws_s3_bucket_public_access_block",
                "change": {"after": {"block_public_acls": False}},
            }
        ]
    }
)

_PLAN_CLEAN = json.dumps(
    {
        "resource_changes": [
            {
                "address": "aws_s3_bucket_public_access_block.good",
                "type": "aws_s3_bucket_public_access_block",
                "change": {"after": {"block_public_acls": True}},
            }
        ]
    }
)


async def _create(client, admin_token, **body) -> dict:
    body.setdefault("name", f"p-{uuid.uuid4().hex[:6]}")
    body.setdefault("rego", _REGO_PUBLIC)
    r = await client.post("/api/v1/policies", json=body, headers=_h(admin_token))
    assert r.status_code == 201, r.text
    return r.json()


# ─── CRUD + RBAC ─────────────────────────────────────────────────────────────


async def test_create_requires_admin(auth_client, operator_token, viewer_token):
    for tok in (operator_token, viewer_token):
        r = await auth_client.post(
            "/api/v1/policies",
            json={"name": "x", "rego": _REGO_PUBLIC},
            headers=_h(tok),
        )
        assert r.status_code == 403, r.text


async def test_create_list_get(auth_client, admin_token, viewer_token):
    created = await _create(auth_client, admin_token, name="no-public", severity="block")
    assert created["current_version"] == 1
    assert created["severity"] == "block"

    # viewer can read
    r = await auth_client.get("/api/v1/policies", headers=_h(viewer_token))
    assert r.status_code == 200
    assert any(p["name"] == "no-public" for p in r.json())

    r = await auth_client.get(f"/api/v1/policies/{created['id']}", headers=_h(viewer_token))
    assert r.status_code == 200
    assert r.json()["rego"] == _REGO_PUBLIC


async def test_duplicate_name_409(auth_client, admin_token):
    await _create(auth_client, admin_token, name="dupe")
    r = await auth_client.post(
        "/api/v1/policies",
        json={"name": "dupe", "rego": _REGO_PUBLIC},
        headers=_h(admin_token),
    )
    assert r.status_code == 409, r.text


async def test_delete(auth_client, admin_token):
    created = await _create(auth_client, admin_token)
    r = await auth_client.delete(f"/api/v1/policies/{created['id']}", headers=_h(admin_token))
    assert r.status_code == 200
    r = await auth_client.get(f"/api/v1/policies/{created['id']}", headers=_h(admin_token))
    assert r.status_code == 404


# ─── version history ─────────────────────────────────────────────────────────


async def test_versions_and_restore(auth_client, admin_token):
    created = await _create(auth_client, admin_token, name="versioned", severity="warn")
    pid = created["id"]

    # Edit → version 2.
    r = await auth_client.put(
        f"/api/v1/policies/{pid}",
        json={"severity": "block", "rego": "package main\n"},
        headers=_h(admin_token),
    )
    assert r.status_code == 200
    assert r.json()["current_version"] == 2
    assert r.json()["severity"] == "block"

    r = await auth_client.get(f"/api/v1/policies/{pid}/versions", headers=_h(admin_token))
    assert r.status_code == 200
    versions = r.json()
    assert {v["version"] for v in versions} == {1, 2}

    # Restore v1 → becomes version 3 with v1's content (severity warn).
    r = await auth_client.post(
        f"/api/v1/policies/{pid}/versions/1/restore", headers=_h(admin_token)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["current_version"] == 3
    assert body["severity"] == "warn"
    assert body["rego"] == _REGO_PUBLIC


# ─── BU scoping ──────────────────────────────────────────────────────────────


async def test_bu_isolation(auth_client, admin_token, _setup_db):
    """A policy in BU A is invisible when acting in BU B."""
    from app.models.business_unit import BusinessUnit, UserBusinessUnit
    from app.models.user import User
    from sqlalchemy import select

    created = await _create(auth_client, admin_token, name="bu-a-only")

    # Make a second BU and add the admin user to it.
    async with _setup_db() as session:
        other = BusinessUnit(id=str(uuid.uuid4()), slug="other", name="Other")
        session.add(other)
        admin = (
            await session.execute(select(User).where(User.email == "admin@test.com"))
        ).scalars().first()
        session.add(UserBusinessUnit(user_id=admin.id, business_unit_id=other.id, role="operator"))
        await session.commit()

    r = await auth_client.get("/api/v1/policies", headers=_h(admin_token, bu="other"))
    assert r.status_code == 200
    assert all(p["name"] != "bu-a-only" for p in r.json())

    r = await auth_client.get(
        f"/api/v1/policies/{created['id']}", headers=_h(admin_token, bu="other")
    )
    assert r.status_code == 404


# ─── gate config (/integrations/opa) ─────────────────────────────────────────


async def test_opa_config_roundtrip(auth_client, admin_token, viewer_token):
    # Default is off + inherited.
    r = await auth_client.get("/api/v1/integrations/opa", headers=_h(viewer_token))
    assert r.status_code == 200
    assert r.json()["mode"] == "off"
    assert r.json()["inherited"] is True

    r = await auth_client.put(
        "/api/v1/integrations/opa",
        json={
            "mode": "enforce",
            "use_bundled": False,
            "bundled_severity": "warn",
            "git_severity": "info",
            "repo_url": "https://example.com/policies.git",
            "repo_ref": "main",
            "repo_dir": "rego",
        },
        headers=_h(admin_token),
    )
    assert r.status_code == 200, r.text

    r = await auth_client.get("/api/v1/integrations/opa", headers=_h(viewer_token))
    body = r.json()
    assert body["mode"] == "enforce"
    assert body["use_bundled"] is False
    assert body["bundled_severity"] == "warn"
    assert body["repo_url"] == "https://example.com/policies.git"
    assert body["inherited"] is False

    # Writing config requires admin.
    r = await auth_client.put(
        "/api/v1/integrations/opa", json={"mode": "off"}, headers=_h(viewer_token)
    )
    assert r.status_code == 403


# ─── executor bundle endpoint ────────────────────────────────────────────────


async def test_run_policies_bundle(auth_client, admin_token, operator_token, _setup_db, default_bu):
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    await _create(auth_client, admin_token, name="enabled-rule", enabled=True)
    await _create(auth_client, admin_token, name="disabled-rule", enabled=False)

    async with _setup_db() as session:
        ws = Workspace(
            business_unit_id=default_bu,
            name="ws1",
            aws_account_id="123456789012",
            region="us-east-1",
            environment="dev",
            tf_working_dir="envs/dev/ws1",
        )
        session.add(ws)
        await session.commit()
        run = Run(id=str(uuid.uuid4()), workspace_id=ws.id, command="plan", status=RunStatus.RUNNING)
        session.add(run)
        await session.commit()
        run_id = run.id

    r = await auth_client.get(f"/api/v1/runs/{run_id}/policies", headers=_h(operator_token))
    assert r.status_code == 200, r.text
    body = r.json()
    names = [p["name"] for p in body["policies"]]
    assert "enabled-rule" in names
    assert "disabled-rule" not in names  # only enabled policies are bundled


# ─── conftest engine ─────────────────────────────────────────────────────────


@needs_conftest
async def test_evaluate_detects_violation(auth_client, admin_token, operator_token):
    await _create(auth_client, admin_token, name="no-public-eval", severity="block")
    r = await auth_client.post(
        "/api/v1/policies/test",
        json={"plan_json": _PLAN_PUBLIC},
        headers=_h(operator_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert any("Public access" in v["msg"] for v in body["violations"])
    assert body["violations"][0]["severity"] == "block"


@needs_conftest
async def test_evaluate_clean_plan_passes(auth_client, admin_token, operator_token):
    await _create(auth_client, admin_token, name="no-public-clean", severity="block")
    r = await auth_client.post(
        "/api/v1/policies/test",
        json={"plan_json": _PLAN_CLEAN},
        headers=_h(operator_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["violations"] == []


@needs_conftest
async def test_evaluate_candidate_rego(auth_client, operator_token):
    """An ad-hoc candidate rule (no stored policy) evaluates against a plan."""
    r = await auth_client.post(
        "/api/v1/policies/test",
        json={"rego": _REGO_PUBLIC, "rego_name": "draft", "plan_json": _PLAN_PUBLIC},
        headers=_h(operator_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["violations"][0]["policy"] == "draft"


@needs_conftest
async def test_verify_unit_tests(auth_client, operator_token):
    tests_rego = """package main

import future.keywords.if

test_denies_public if {
    deny[_] with input as {"resource_changes": [
        {"address": "x", "type": "aws_s3_bucket_public_access_block",
         "change": {"after": {"block_public_acls": false}}}
    ]}
}
"""
    r = await auth_client.post(
        "/api/v1/policies/verify",
        json={"rego": _REGO_PUBLIC, "tests_rego": tests_rego},
        headers=_h(operator_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


async def test_test_endpoint_requires_plan_source(auth_client, operator_token):
    r = await auth_client.post(
        "/api/v1/policies/test", json={"rego": _REGO_PUBLIC}, headers=_h(operator_token)
    )
    assert r.status_code == 400
