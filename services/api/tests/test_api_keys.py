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


# ─── rotation with an overlap window ────────────────────────────────────────


async def _rotate(client, admin_token, key_id, **body) -> dict:
    r = await client.post(
        f"/api/v1/api-keys/{key_id}/rotate", json=body or {}, headers=_h(admin_token)
    )
    assert r.status_code == 201, r.text
    return r.json()


def _as_utc(value: str) -> datetime:
    """Parse a timestamp from the API, tolerating a missing offset.

    SQLite does not persist tzinfo, so under the test DB a `DateTime(timezone=
    True)` column round-trips naive; Postgres returns it aware. The endpoint
    normalizes the same way before comparing, so this mirrors production
    behaviour rather than papering over a bug.
    """
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _authenticates(client, token) -> bool:
    r = await client.get("/api/v1/workspaces", headers=_h(token))
    assert r.status_code in (200, 401), r.text
    return r.status_code == 200


@pytest.mark.asyncio
async def test_both_secrets_work_during_the_overlap(auth_client, admin_token, default_bu):
    """The entire point of the feature.

    `regenerate` kills the old secret instantly, which is unusable when the same
    key lives in CI, a laptop keychain and a cron job — you cannot update all
    three atomically. During the window both must authenticate.
    """
    old = await _mint(auth_client, admin_token, name="ci", capability="read")
    rotated = await _rotate(auth_client, admin_token, old["id"], overlap_hours=24)

    assert rotated["token"] != old["token"]
    assert await _authenticates(auth_client, old["token"]), "old secret died immediately"
    assert await _authenticates(auth_client, rotated["token"]), "new secret does not work"


@pytest.mark.asyncio
async def test_rotation_sets_the_predecessor_deadline(auth_client, admin_token, default_bu):
    old = await _mint(auth_client, admin_token, name="ci")
    before = datetime.now(timezone.utc)
    rotated = await _rotate(auth_client, admin_token, old["id"], overlap_hours=6)

    assert rotated["predecessor_id"] == old["id"]
    deadline = _as_utc(rotated["predecessor_expires_at"])
    delta = deadline - before
    assert timedelta(hours=5, minutes=55) < delta < timedelta(hours=6, minutes=5)


@pytest.mark.asyncio
async def test_old_key_stops_working_once_the_window_closes(
    auth_client, admin_token, default_bu, _setup_db
):
    """No sweeper needed: the overlap rides the existing `expires_at` check."""
    from app.models.api_key import APIKey

    old = await _mint(auth_client, admin_token, name="ci")
    await _rotate(auth_client, admin_token, old["id"], overlap_hours=1)
    assert await _authenticates(auth_client, old["token"])

    # Wind the clock past the window rather than sleeping through it.
    async with _setup_db() as session:
        row = await session.get(APIKey, old["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    assert not await _authenticates(auth_client, old["token"]), "expired key still works"


@pytest.mark.asyncio
async def test_zero_overlap_is_an_immediate_cutover(auth_client, admin_token, default_bu):
    old = await _mint(auth_client, admin_token, name="ci")
    rotated = await _rotate(auth_client, admin_token, old["id"], overlap_hours=0)
    assert not await _authenticates(auth_client, old["token"])
    assert await _authenticates(auth_client, rotated["token"])


@pytest.mark.asyncio
async def test_default_overlap_is_24h(auth_client, admin_token, default_bu):
    old = await _mint(auth_client, admin_token, name="ci")
    r = await auth_client.post(
        f"/api/v1/api-keys/{old['id']}/rotate", json={}, headers=_h(admin_token)
    )
    assert r.status_code == 201, r.text
    deadline = _as_utc(r.json()["predecessor_expires_at"])
    assert timedelta(hours=23) < deadline - datetime.now(timezone.utc) < timedelta(hours=25)


@pytest.mark.asyncio
async def test_rotation_never_extends_a_shorter_expiry(auth_client, admin_token, default_bu):
    """A key expiring in 2h must not gain 22 more hours by being rotated."""
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    old = await _mint(auth_client, admin_token, name="ci", expires_at=soon.isoformat())
    rotated = await _rotate(auth_client, admin_token, old["id"], overlap_hours=24)
    deadline = _as_utc(rotated["predecessor_expires_at"])
    assert deadline <= soon + timedelta(seconds=1), "rotation extended the credential's life"


@pytest.mark.asyncio
async def test_successor_inherits_scope_and_owner(auth_client, admin_token, default_bu, _setup_db):
    ws_id = await _seed_workspace(_setup_db) if "_seed_workspace" in globals() else None
    old = await _mint(auth_client, admin_token, name="scoped", capability="plan")
    rotated = await _rotate(auth_client, admin_token, old["id"])
    for field in ("name", "capability", "business_unit_id", "user_id", "workspace_ids"):
        assert rotated[field] == old[field], f"{field} not carried to the successor"


@pytest.mark.asyncio
async def test_successor_gets_a_clean_usage_trail(auth_client, admin_token, default_bu):
    old = await _mint(auth_client, admin_token, name="ci")
    await _authenticates(auth_client, old["token"])  # stamp last_used_at
    rotated = await _rotate(auth_client, admin_token, old["id"])
    assert rotated["last_used_at"] is None
    assert rotated["rotated_at"] is None
    assert rotated["superseded_by_id"] is None


@pytest.mark.asyncio
async def test_predecessor_records_the_link(auth_client, admin_token, default_bu):
    old = await _mint(auth_client, admin_token, name="ci")
    rotated = await _rotate(auth_client, admin_token, old["id"])
    r = await auth_client.get("/api/v1/api-keys", headers=_h(admin_token))
    rows = {k["id"]: k for k in r.json()}
    assert rows[old["id"]]["superseded_by_id"] == rotated["id"]
    assert rows[old["id"]]["rotated_at"] is not None


@pytest.mark.asyncio
async def test_cannot_rotate_the_same_key_twice(auth_client, admin_token, default_bu):
    """Chaining would leave several live secrets behind one name."""
    old = await _mint(auth_client, admin_token, name="ci")
    first = await _rotate(auth_client, admin_token, old["id"])
    r = await auth_client.post(
        f"/api/v1/api-keys/{old['id']}/rotate", json={}, headers=_h(admin_token)
    )
    assert r.status_code == 409
    assert first["id"] in r.json()["detail"]


@pytest.mark.asyncio
async def test_can_rotate_the_successor(auth_client, admin_token, default_bu):
    old = await _mint(auth_client, admin_token, name="ci")
    first = await _rotate(auth_client, admin_token, old["id"])
    second = await _rotate(auth_client, admin_token, first["id"])
    assert second["predecessor_id"] == first["id"]
    assert await _authenticates(auth_client, second["token"])


@pytest.mark.asyncio
async def test_cannot_rotate_a_revoked_or_expired_key(auth_client, admin_token, default_bu, _setup_db):
    from app.models.api_key import APIKey

    revoked = await _mint(auth_client, admin_token, name="dead")
    await auth_client.delete(f"/api/v1/api-keys/{revoked['id']}", headers=_h(admin_token))
    r = await auth_client.post(
        f"/api/v1/api-keys/{revoked['id']}/rotate", json={}, headers=_h(admin_token)
    )
    assert r.status_code == 409 and "revoked" in r.json()["detail"].lower()

    stale = await _mint(auth_client, admin_token, name="old")
    async with _setup_db() as session:
        row = await session.get(APIKey, stale["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
    r = await auth_client.post(
        f"/api/v1/api-keys/{stale['id']}/rotate", json={}, headers=_h(admin_token)
    )
    assert r.status_code == 409 and "expired" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_overlap_hours_is_range_checked(auth_client, admin_token, default_bu):
    old = await _mint(auth_client, admin_token, name="ci")
    for bad in (-1, 24 * 7 + 1):
        r = await auth_client.post(
            f"/api/v1/api-keys/{old['id']}/rotate",
            json={"overlap_hours": bad},
            headers=_h(admin_token),
        )
        assert r.status_code == 422, f"overlap_hours={bad} accepted"


@pytest.mark.asyncio
async def test_rotation_is_audited(auth_client, admin_token, default_bu, _setup_db):
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    old = await _mint(auth_client, admin_token, name="ci")
    rotated = await _rotate(auth_client, admin_token, old["id"], overlap_hours=12)

    async with _setup_db() as session:
        rows = (await session.execute(
            select(AuditLog).where(AuditLog.action == "api_key.rotate")
        )).scalars().all()
    assert len(rows) == 1
    details = rows[0].details
    assert details["successor_id"] == rotated["id"]
    assert details["overlap_hours"] == 12
    # The plaintext must never reach the audit log.
    assert rotated["token"] not in str(details)


@pytest.mark.asyncio
async def test_rotation_is_scoped_to_the_bu(auth_client, admin_token, default_bu, _setup_db):
    """A key in another BU must be invisible, not merely forbidden."""
    from app.models.business_unit import BusinessUnit
    from app.models.api_key import APIKey
    from app.services import api_key_service as svc

    other_bu, other_key = str(uuid.uuid4()), str(uuid.uuid4())
    _, prefix, token_hash = svc.generate_token()
    async with _setup_db() as session:
        session.add(BusinessUnit(id=other_bu, slug="other-rot", name="Other"))
        await session.commit()
        me = (await session.execute(
            __import__("sqlalchemy").select(APIKey).limit(1)
        )).scalars().first()
        session.add(APIKey(
            id=other_key, name="foreign", token_prefix=prefix, token_hash=token_hash,
            user_id=(me.user_id if me else None) or str(uuid.uuid4()),
            business_unit_id=other_bu, capability="read",
        ))
        await session.commit()

    r = await auth_client.post(
        f"/api/v1/api-keys/{other_key}/rotate", json={}, headers=_h(admin_token)
    )
    assert r.status_code == 404
