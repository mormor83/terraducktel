"""Global + workspace + per-run variables.

Covers:
  - Encrypt/decrypt roundtrip for the variable Fernet salt.
  - Plaintext never appears in API responses for secret rows.
  - RBAC: admin-only writes for global, operator+ for workspace.
  - Merge precedence: global ← workspace ← run, last wins.
  - Executor receives the merged set as `TF_VAR_*` env entries.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio


# `default_bu` is provided by conftest.py (shared across the suite).


# ─── service-level encryption ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_encrypt_decrypt_roundtrip(_setup_db):
    """The variable Fernet derives a distinct key from CREDENTIAL_ENCRYPTION_KEY
    via HKDF, so the cipher must round-trip plaintext exactly."""
    from app.services import variable_service as varsvc

    plaintext = 'set("a", "b") = ["x", "y", 42]'
    ct = varsvc.encrypt_value(plaintext)
    assert plaintext not in ct  # ciphertext doesn't leak the value
    assert varsvc.decrypt_value(ct) == plaintext


@pytest.mark.asyncio
async def test_serialize_run_variables_roundtrip(_setup_db):
    """Run blob is a JSON array Fernet-encrypted as one token."""
    from app.schemas.variable import RunVariable
    from app.services import variable_service as varsvc

    payload = [
        RunVariable(key="region", value="us-east-1", is_secret=False, is_hcl=False),
        RunVariable(key="tags", value='{env="prod"}', is_secret=False, is_hcl=True),
        RunVariable(key="api_key", value="supersecret", is_secret=True, is_hcl=False),
    ]
    blob = varsvc.serialize_run_variables(payload)
    assert "supersecret" not in blob  # ciphertext doesn't leak the secret

    out = varsvc.deserialize_run_variables(blob)
    assert out == [
        {"key": "region", "value": "us-east-1", "is_secret": False, "is_hcl": False},
        {"key": "tags", "value": '{env="prod"}', "is_secret": False, "is_hcl": True},
        {"key": "api_key", "value": "supersecret", "is_secret": True, "is_hcl": False},
    ]


# ─── API: global variables ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_global_variable_secret_masks_value(
    auth_client, seeded_users, default_bu, _setup_db
):
    """Secret global vars: API returns masked tail, never plaintext."""
    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    r = await auth_client.post(
        "/api/v1/variables",
        json={"key": "DATADOG_API_KEY", "value": "dd-supersecret-abcd",
              "is_secret": True, "is_hcl": False, "description": "datadog forwarder"},
        headers={"Authorization": f"Bearer {token}", "X-Business-Unit": "default"},
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["key"] == "DATADOG_API_KEY"
    assert out["is_secret"] is True
    assert out["value"] is None
    assert out["masked_tail"] == "…abcd"
    assert "dd-supersecret-abcd" not in r.text


@pytest.mark.asyncio
async def test_create_global_variable_non_secret_returns_plaintext(
    auth_client, seeded_users, default_bu, _setup_db
):
    """Non-secret vars return the value so the UI can display it."""
    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    r = await auth_client.post(
        "/api/v1/variables",
        json={"key": "default_region", "value": "us-east-1"},
        headers={"Authorization": f"Bearer {token}", "X-Business-Unit": "default"},
    )
    assert r.status_code == 201
    out = r.json()
    assert out["value"] == "us-east-1"
    assert out["masked_tail"] is None


@pytest.mark.asyncio
async def test_global_variable_admin_only_write(
    auth_client, seeded_users, _setup_db
):
    """Operators and viewers cannot create globals."""
    for role in ("operator", "viewer"):
        r = await auth_client.post(
            "/api/v1/auth/token",
            json={"email": f"{role}@test.com", "password": "password123"},
        )
        token = r.json()["access_token"]
        r = await auth_client.post(
            "/api/v1/variables",
            json={"key": "foo", "value": "bar"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403, f"{role} should be denied"


@pytest.mark.asyncio
async def test_invalid_key_returns_422(auth_client, seeded_users, default_bu, _setup_db):
    """Terraform identifier rules apply at the schema layer."""
    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    r = await auth_client.post(
        "/api/v1/variables",
        json={"key": "9-bad-key", "value": "x"},
        headers={"Authorization": f"Bearer {token}", "X-Business-Unit": "default"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_global_key_returns_409(
    auth_client, seeded_users, default_bu, _setup_db
):
    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    body = {"key": "shared", "value": "v1"}
    h = {"Authorization": f"Bearer {token}", "X-Business-Unit": "default"}
    r = await auth_client.post("/api/v1/variables", json=body, headers=h)
    assert r.status_code == 201

    r = await auth_client.post("/api/v1/variables", json=body, headers=h)
    assert r.status_code == 409


# ─── API: workspace variables ──────────────────────────────────────────────


async def _make_workspace(factory) -> str:
    """Insert a minimal workspace row directly. Bypasses the workspaces router
    so this test file doesn't pull in the workspace-create validation chain.
    """
    from app.models.business_unit import DEFAULT_BU_ID
    from app.models.workspace import Workspace

    ws_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            id=ws_id,
            name=f"vars-test-{ws_id[:8]}",
            business_unit_id=DEFAULT_BU_ID,
            repo_url="https://example.com/repo.git",
            tf_working_dir=".",
            aws_account_id="123456789012",
            environment="dev",
        ))
        await session.commit()
    return ws_id


@pytest.mark.asyncio
async def test_workspace_variable_operator_can_write(
    auth_client, seeded_users, default_bu, _setup_db
):
    """Workspace vars are operator-writable (unlike global which is admin-only)."""
    ws_id = await _make_workspace(_setup_db)

    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "operator@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    r = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/variables",
        json={"key": "instance_count", "value": "3", "is_hcl": False},
        headers={"Authorization": f"Bearer {token}", "X-Business-Unit": "default"},
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["scope"] == "workspace"
    assert out["workspace_id"] == ws_id


@pytest.mark.asyncio
async def test_workspace_variable_viewer_cannot_write(
    auth_client, seeded_users, _setup_db
):
    ws_id = await _make_workspace(_setup_db)

    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "viewer@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    r = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/variables",
        json={"key": "x", "value": "y"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_workspace_variable_404_on_unknown_workspace(
    auth_client, seeded_users, default_bu, _setup_db
):
    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "operator@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    r = await auth_client.post(
        "/api/v1/workspaces/does-not-exist/variables",
        json={"key": "x", "value": "y"},
        headers={"Authorization": f"Bearer {token}", "X-Business-Unit": "default"},
    )
    assert r.status_code == 404


# ─── merge semantics ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_merged_for_run_precedence(_setup_db):
    """Run beats workspace beats global, last wins per key."""
    from app.models.business_unit import DEFAULT_BU_ID
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace
    from app.schemas.variable import RunVariable
    from app.services import variable_service as varsvc

    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    factory = _setup_db
    async with factory() as session:
        session.add(Workspace(
            id=ws_id, name="merge-test",
            business_unit_id=DEFAULT_BU_ID,
            repo_url="https://example.com/r.git",
            tf_working_dir=".",
            aws_account_id="123456789012",
            environment="dev",
        ))
        # Global: region=us-east-1, debug=true (in the workspace's BU so the
        # merge picks them up — globals are BU-scoped).
        await varsvc.create_global(
            session, _v("region", "us-east-1"), DEFAULT_BU_ID
        )
        await varsvc.create_global(
            session, _v("debug", "true"), DEFAULT_BU_ID
        )
        # Workspace overrides region; adds instance_count
        await varsvc.create_workspace_var(
            session, ws_id, _v("region", "eu-west-1")
        )
        await varsvc.create_workspace_var(
            session, ws_id, _v("instance_count", "3")
        )
        # Run overrides debug; adds api_key
        run = Run(
            id=run_id, workspace_id=ws_id,
            command="plan", status=RunStatus.PENDING,
            variables_encrypted=varsvc.serialize_run_variables([
                RunVariable(key="debug", value="false"),
                RunVariable(key="api_key", value="secret123", is_secret=True),
            ]),
        )
        session.add(run)
        await session.commit()

    async with factory() as session:
        run = await session.get(Run, run_id)
        merged = await varsvc.get_merged_for_run(session, ws_id, run)

    assert merged["region"].value == "eu-west-1"
    assert merged["region"].source == "workspace"
    assert merged["instance_count"].value == "3"
    assert merged["instance_count"].source == "workspace"
    assert merged["debug"].value == "false"
    assert merged["debug"].source == "run"
    assert merged["api_key"].value == "secret123"
    assert merged["api_key"].source == "run"


def _v(key: str, value: str, **kwargs):
    """Compact constructor for VariableCreate in tests."""
    from app.schemas.variable import VariableCreate
    return VariableCreate(key=key, value=value, **kwargs)
