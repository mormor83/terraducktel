"""API key minting, authentication, and scoped enforcement.

Covers:
  - Admin-only minting; viewer/operator are rejected.
  - The plaintext token is returned exactly once and never again.
  - An API key authenticates as its owner but is capped at its capability tier
    (read/plan/apply/admin) and (optionally) a workspace allowlist.
  - Tenancy is forced to the key's BU regardless of X-Business-Unit.
  - Revoked / expired keys fail authentication (401).
  - `admin` keys reach the full admin surface (workspace CRUD, locks) *within
    their BU* but are still walled off from identity (key/user/BU management).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio


# `default_bu` is provided by conftest.py (shared across the suite).


async def _make_bu(factory, slug: str, name: str) -> str:
    from app.models.business_unit import BusinessUnit

    async with factory() as session:
        bu = BusinessUnit(id=str(uuid.uuid4()), slug=slug, name=name)
        session.add(bu)
        await session.commit()
        return bu.id


async def _make_workspace(factory, bu_id: str, name: str) -> str:
    from app.models.workspace import Workspace

    async with factory() as session:
        ws = Workspace(
            business_unit_id=bu_id,
            name=name,
            aws_account_id="123456789012",
            region="us-east-1",
            environment="dev",
            tf_working_dir=f"envs/dev/{name}",
        )
        session.add(ws)
        await session.commit()
        return ws.id


async def _make_run(factory, ws_id: str, status_value=None) -> str:
    from app.models.run import Run, RunStatus

    async with factory() as session:
        run = Run(
            id=str(uuid.uuid4()),
            workspace_id=ws_id,
            triggered_by=None,
            command="apply",
            status=status_value or RunStatus.PENDING,
        )
        session.add(run)
        await session.commit()
        return run.id


def _h(token: str, bu: str = "default") -> dict:
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


async def _mint(client, admin_token, **body) -> dict:
    body.setdefault("name", "k")
    body.setdefault("capability", "read")
    r = await client.post("/api/v1/api-keys", json=body, headers=_h(admin_token))
    assert r.status_code == 201, r.text
    return r.json()


# ─── minting ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_mint_token_returned_once(auth_client, admin_token, default_bu):
    created = await _mint(auth_client, admin_token, name="ci", capability="plan")
    assert created["token"].startswith("tdt_")
    assert created["capability"] == "plan"
    # List never exposes the plaintext.
    r = await auth_client.get("/api/v1/api-keys", headers=_h(admin_token))
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert "token" not in rows[0]
    assert rows[0]["token_prefix"].startswith("tdt_")


@pytest.mark.asyncio
async def test_non_admin_cannot_mint(auth_client, operator_token, viewer_token, default_bu):
    for tok in (operator_token, viewer_token):
        r = await auth_client.post(
            "/api/v1/api-keys", json={"name": "x", "capability": "read"}, headers=_h(tok)
        )
        assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_allowlist_must_be_in_bu(auth_client, admin_token, default_bu):
    r = await auth_client.post(
        "/api/v1/api-keys",
        json={"name": "x", "capability": "plan", "workspace_ids": ["does-not-exist"]},
        headers=_h(admin_token),
    )
    assert r.status_code == 400, r.text


# ─── authentication ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_key_authenticates_and_forces_bu(auth_client, admin_token, default_bu):
    created = await _mint(auth_client, admin_token, capability="read")
    # No X-Business-Unit header at all — the key pins the BU itself.
    r = await auth_client.get(
        "/api/v1/runs", headers={"Authorization": f"Bearer {created['token']}"}
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_revoked_key_401(auth_client, admin_token, default_bu):
    created = await _mint(auth_client, admin_token, capability="read")
    rv = await auth_client.delete(f"/api/v1/api-keys/{created['id']}", headers=_h(admin_token))
    assert rv.status_code == 200, rv.text
    r = await auth_client.get(
        "/api/v1/runs", headers={"Authorization": f"Bearer {created['token']}"}
    )
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_expired_key_401(auth_client, admin_token, default_bu, _setup_db):
    created = await _mint(auth_client, admin_token, capability="read")
    # Backdate the expiry directly in the DB.
    from app.models.api_key import APIKey

    async with _setup_db() as s:
        key = await s.get(APIKey, created["id"])
        key.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await s.commit()
    r = await auth_client.get(
        "/api/v1/runs", headers={"Authorization": f"Bearer {created['token']}"}
    )
    assert r.status_code == 401, r.text


# ─── regenerate (rotate secret in place) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_regenerate_rotates_token_old_dies_new_works(
    auth_client, admin_token, default_bu
):
    created = await _mint(auth_client, admin_token, name="ci", capability="apply")
    old_token, old_prefix = created["token"], created["token_prefix"]

    r = await auth_client.post(
        f"/api/v1/api-keys/{created['id']}/regenerate", headers=_h(admin_token)
    )
    assert r.status_code == 200, r.text
    rotated = r.json()
    # Same row, same settings — fresh secret.
    assert rotated["id"] == created["id"]
    assert rotated["capability"] == "apply"
    assert rotated["token"].startswith("tdt_")
    assert rotated["token"] != old_token
    assert rotated["token_prefix"] != old_prefix

    # Old token is dead; new one authenticates.
    dead = await auth_client.get(
        "/api/v1/runs", headers={"Authorization": f"Bearer {old_token}"}
    )
    assert dead.status_code == 401, dead.text
    alive = await auth_client.get(
        "/api/v1/runs", headers={"Authorization": f"Bearer {rotated['token']}"}
    )
    assert alive.status_code == 200, alive.text


@pytest.mark.asyncio
async def test_regenerate_preserves_workspace_allowlist(
    auth_client, admin_token, default_bu, _setup_db
):
    ws_a = await _make_workspace(_setup_db, default_bu, "alpha")
    ws_b = await _make_workspace(_setup_db, default_bu, "beta")
    created = await _mint(
        auth_client, admin_token, capability="apply", workspace_ids=[ws_a]
    )
    r = await auth_client.post(
        f"/api/v1/api-keys/{created['id']}/regenerate", headers=_h(admin_token)
    )
    assert r.status_code == 200, r.text
    hk = {"Authorization": f"Bearer {r.json()['token']}"}
    # Allowlist survives the rotation.
    ok = await auth_client.post(
        f"/api/v1/workspaces/{ws_a}/runs", json={"command": "plan"}, headers=hk
    )
    assert ok.status_code == 201, ok.text
    denied = await auth_client.post(
        f"/api/v1/workspaces/{ws_b}/runs", json={"command": "plan"}, headers=hk
    )
    assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
async def test_regenerate_rejects_revoked_key(auth_client, admin_token, default_bu):
    """A dead key can't be revived by rotating its secret — recreate instead."""
    created = await _mint(auth_client, admin_token, capability="read")
    rv = await auth_client.delete(
        f"/api/v1/api-keys/{created['id']}", headers=_h(admin_token)
    )
    assert rv.status_code == 200, rv.text

    r = await auth_client.post(
        f"/api/v1/api-keys/{created['id']}/regenerate", headers=_h(admin_token)
    )
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_regenerate_rejects_expired_key(
    auth_client, admin_token, default_bu, _setup_db
):
    """Rotating the secret doesn't move the expiry, so an expired key stays
    expired — regenerate refuses it rather than minting a born-expired token."""
    created = await _mint(auth_client, admin_token, capability="read")
    from app.models.api_key import APIKey

    async with _setup_db() as s:
        key = await s.get(APIKey, created["id"])
        key.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await s.commit()

    r = await auth_client.post(
        f"/api/v1/api-keys/{created['id']}/regenerate", headers=_h(admin_token)
    )
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_regenerate_unknown_key_404(auth_client, admin_token, default_bu):
    r = await auth_client.post(
        "/api/v1/api-keys/does-not-exist/regenerate", headers=_h(admin_token)
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_non_admin_and_keys_cannot_regenerate(
    auth_client, admin_token, operator_token, default_bu
):
    created = await _mint(auth_client, admin_token, capability="apply")
    path = f"/api/v1/api-keys/{created['id']}/regenerate"
    # Operator (JWT) is rejected by require_role(admin).
    assert (
        await auth_client.post(path, headers=_h(operator_token))
    ).status_code == 403
    # An admin-tier key is rejected by the router's forbid_api_keys.
    hk = {"Authorization": f"Bearer {created['token']}"}
    assert (await auth_client.post(path, headers=hk)).status_code == 403


# ─── capability gating ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_key_cannot_trigger(auth_client, admin_token, default_bu, _setup_db):
    ws = await _make_workspace(_setup_db, default_bu, "vpc")
    created = await _mint(auth_client, admin_token, capability="read")
    r = await auth_client.post(
        f"/api/v1/workspaces/{ws}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_plan_key_triggers_plan_but_not_apply(
    auth_client, admin_token, default_bu, _setup_db
):
    ws = await _make_workspace(_setup_db, default_bu, "vpc")
    created = await _mint(auth_client, admin_token, capability="plan")
    hk = {"Authorization": f"Bearer {created['token']}"}

    ok = await auth_client.post(
        f"/api/v1/workspaces/{ws}/runs", json={"command": "plan"}, headers=hk
    )
    assert ok.status_code == 201, ok.text

    denied = await auth_client.post(
        f"/api/v1/workspaces/{ws}/runs", json={"command": "apply"}, headers=hk
    )
    assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
async def test_plan_key_cannot_approve_apply_key_can(
    auth_client, admin_token, default_bu, _setup_db
):
    from app.models.run import RunStatus

    ws = await _make_workspace(_setup_db, default_bu, "vpc")
    run_id = await _make_run(_setup_db, ws, RunStatus.AWAITING_APPROVAL)

    plan_key = await _mint(auth_client, admin_token, name="p", capability="plan")
    apply_key = await _mint(auth_client, admin_token, name="a", capability="apply")

    denied = await auth_client.post(
        f"/api/v1/runs/{run_id}/approve",
        json={},
        headers={"Authorization": f"Bearer {plan_key['token']}"},
    )
    assert denied.status_code == 403, denied.text

    ok = await auth_client.post(
        f"/api/v1/runs/{run_id}/approve",
        json={},
        headers={"Authorization": f"Bearer {apply_key['token']}"},
    )
    assert ok.status_code == 200, ok.text


# ─── workspace allowlist ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workspace_allowlist_blocks_other_workspace(
    auth_client, admin_token, default_bu, _setup_db
):
    ws_a = await _make_workspace(_setup_db, default_bu, "alpha")
    ws_b = await _make_workspace(_setup_db, default_bu, "beta")
    created = await _mint(
        auth_client, admin_token, capability="apply", workspace_ids=[ws_a]
    )
    hk = {"Authorization": f"Bearer {created['token']}"}

    ok = await auth_client.post(
        f"/api/v1/workspaces/{ws_a}/runs", json={"command": "plan"}, headers=hk
    )
    assert ok.status_code == 201, ok.text

    denied = await auth_client.post(
        f"/api/v1/workspaces/{ws_b}/runs", json={"command": "plan"}, headers=hk
    )
    assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
async def test_allowlist_filters_run_list(auth_client, admin_token, default_bu, _setup_db):
    from app.models.run import RunStatus

    ws_a = await _make_workspace(_setup_db, default_bu, "alpha")
    ws_b = await _make_workspace(_setup_db, default_bu, "beta")
    run_a = await _make_run(_setup_db, ws_a, RunStatus.PENDING)
    await _make_run(_setup_db, ws_b, RunStatus.PENDING)

    created = await _mint(
        auth_client, admin_token, capability="read", workspace_ids=[ws_a]
    )
    r = await auth_client.get(
        "/api/v1/runs", headers={"Authorization": f"Bearer {created['token']}"}
    )
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert ids == {run_a}


# ─── keys can never reach admin endpoints ─────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_key_cannot_mutate_workspace(auth_client, admin_token, default_bu, _setup_db):
    """API keys drive runs, not workspace config. Even an apply key scoped to the
    exact workspace is blocked from PUT (update) and force-unlock — these are
    interactive-only, independent of tier/allowlist."""
    ws = await _make_workspace(_setup_db, default_bu, "vpc")
    key = await _mint(auth_client, admin_token, capability="apply", workspace_ids=[ws])
    hk = {"Authorization": f"Bearer {key['token']}"}

    upd = await auth_client.put(f"/api/v1/workspaces/{ws}", json={"name": "renamed"}, headers=hk)
    assert upd.status_code == 403, upd.text

    unlock = await auth_client.delete(f"/api/v1/workspaces/{ws}/state-lock", headers=hk)
    assert unlock.status_code == 403, unlock.text


@pytest.mark.asyncio
async def test_apply_key_cannot_manage_keys(auth_client, admin_token, default_bu):
    """Even an apply key (owned by an admin) is capped at operator, so the
    admin-only key-management endpoints stay closed to automation."""
    created = await _mint(auth_client, admin_token, capability="apply")
    hk = {"Authorization": f"Bearer {created['token']}"}
    r = await auth_client.get("/api/v1/api-keys", headers=hk)
    assert r.status_code == 403, r.text
    r2 = await auth_client.post(
        "/api/v1/api-keys", json={"name": "nope", "capability": "read"}, headers=hk
    )
    assert r2.status_code == 403, r2.text


# ─── admin tier: full control within the BU ───────────────────────────────────


@pytest.mark.asyncio
async def test_admin_key_can_update_workspace(auth_client, admin_token, default_bu, _setup_db):
    """An `admin`-tier key may reconfigure a workspace — the action an apply key
    is blocked from (`test_apply_key_cannot_mutate_workspace`)."""
    ws = await _make_workspace(_setup_db, default_bu, "vpc")
    key = await _mint(auth_client, admin_token, name="adm", capability="admin")
    hk = {"Authorization": f"Bearer {key['token']}"}

    upd = await auth_client.put(
        f"/api/v1/workspaces/{ws}", json={"name": "renamed"}, headers=hk
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["name"] == "renamed"


@pytest.mark.asyncio
async def test_admin_key_can_force_release_lock(auth_client, admin_token, default_bu, _setup_db):
    ws = await _make_workspace(_setup_db, default_bu, "vpc")
    key = await _mint(auth_client, admin_token, name="adm", capability="admin")
    hk = {"Authorization": f"Bearer {key['token']}"}

    unlock = await auth_client.delete(f"/api/v1/workspaces/{ws}/state-lock", headers=hk)
    assert unlock.status_code == 204, unlock.text


@pytest.mark.asyncio
async def test_admin_key_can_apply_and_approve(auth_client, admin_token, default_bu, _setup_db):
    """admin >= apply, so it inherits run trigger + approve."""
    from app.models.run import RunStatus

    ws = await _make_workspace(_setup_db, default_bu, "vpc")
    run_id = await _make_run(_setup_db, ws, RunStatus.AWAITING_APPROVAL)
    key = await _mint(auth_client, admin_token, name="adm", capability="admin")
    hk = {"Authorization": f"Bearer {key['token']}"}

    ok = await auth_client.post(f"/api/v1/runs/{run_id}/approve", json={}, headers=hk)
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_admin_key_allowlist_still_applies(auth_client, admin_token, default_bu, _setup_db):
    """The workspace allowlist confines an admin key just like any other tier."""
    ws_a = await _make_workspace(_setup_db, default_bu, "alpha")
    ws_b = await _make_workspace(_setup_db, default_bu, "beta")
    key = await _mint(
        auth_client, admin_token, name="adm", capability="admin", workspace_ids=[ws_a]
    )
    hk = {"Authorization": f"Bearer {key['token']}"}

    ok = await auth_client.put(f"/api/v1/workspaces/{ws_a}", json={"name": "a2"}, headers=hk)
    assert ok.status_code == 200, ok.text

    denied = await auth_client.put(f"/api/v1/workspaces/{ws_b}", json={"name": "b2"}, headers=hk)
    assert denied.status_code == 403, denied.text


# ─── admin tier is STILL walled off from identity + cross-BU ───────────────────


@pytest.mark.asyncio
async def test_admin_key_cannot_manage_keys(auth_client, admin_token, default_bu):
    """The blanket forbid_api_keys on the api-keys router rejects admin keys too
    — a key minting keys would be privilege escalation."""
    key = await _mint(auth_client, admin_token, name="adm", capability="admin")
    hk = {"Authorization": f"Bearer {key['token']}"}
    assert (await auth_client.get("/api/v1/api-keys", headers=hk)).status_code == 403
    r = await auth_client.post(
        "/api/v1/api-keys", json={"name": "nope", "capability": "read"}, headers=hk
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_admin_key_cannot_manage_users(auth_client, admin_token, default_bu):
    key = await _mint(auth_client, admin_token, name="adm", capability="admin")
    hk = {"Authorization": f"Bearer {key['token']}"}
    assert (await auth_client.get("/api/v1/users", headers=hk)).status_code == 403
    r = await auth_client.patch(
        "/api/v1/users/whoever", json={"is_superadmin": True}, headers=hk
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_admin_key_cannot_manage_business_units(auth_client, admin_token, default_bu):
    """admin keys can't create/update BUs, and only ever see their own BU."""
    key = await _mint(auth_client, admin_token, name="adm", capability="admin")
    hk = {"Authorization": f"Bearer {key['token']}"}

    create = await auth_client.post(
        "/api/v1/business-units", json={"slug": "rogue", "name": "Rogue"}, headers=hk
    )
    assert create.status_code == 403, create.text

    # List is allowed but confined to the key's single BU.
    lst = await auth_client.get("/api/v1/business-units", headers=hk)
    assert lst.status_code == 200, lst.text
    rows = lst.json()
    assert len(rows) == 1
    assert rows[0]["id"] == default_bu


@pytest.mark.asyncio
async def test_admin_key_confined_to_its_bu(auth_client, admin_token, default_bu, _setup_db):
    """An admin key cannot touch a workspace in another BU — bu_context pins the
    key's BU and scoped_workspace 404s anything outside it."""
    other_bu = await _make_bu(_setup_db, "other", "Other")
    other_ws = await _make_workspace(_setup_db, other_bu, "their-vpc")
    key = await _mint(auth_client, admin_token, name="adm", capability="admin")
    hk = {"Authorization": f"Bearer {key['token']}"}

    upd = await auth_client.put(
        f"/api/v1/workspaces/{other_ws}", json={"name": "pwned"}, headers=hk
    )
    assert upd.status_code == 404, upd.text
