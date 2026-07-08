"""state_backend validation on workspace create/update.

Exercises routers/workspaces.py `_validate_state_backend_linkage`: a workspace
may only select azureblob/gcs when the matching cloud link + storage target
are present. Also covers the schema-level rejection of unknown backends and
the happy paths.
"""
import json

import pytest

pytestmark = pytest.mark.usefixtures("default_bu")


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


def _sa_json(project_id):
    return json.dumps({
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "k1",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
        "client_email": f"sa@{project_id}.iam.gserviceaccount.com",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    })


async def _mk_azure_sub(client, token, *, with_storage):
    body = {
        "subscription_id": "00000000-0000-0000-0000-000000000abc",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "client_id": "22222222-2222-2222-2222-222222222222",
        "client_secret": "sp-secret",
        "name": "azure-state",
    }
    if with_storage:
        body["state_storage_account"] = "acmetfstate"
        body["state_container"] = "tfstate"
    r = await client.post("/api/v1/azure-subscriptions", json=body, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _mk_gcp_project(client, token, project_id, *, with_bucket):
    body = {
        "project_id": project_id,
        "name": project_id,
        "service_account_json": _sa_json(project_id),
    }
    if with_bucket:
        body["state_bucket"] = f"{project_id}-tfstate"
    r = await client.post("/api/v1/gcp-projects", json=body, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ws_body(default_aws_account, **over):
    b = {
        "name": "ws",
        "environment": "dev",
        "aws_account_id": default_aws_account,
        "region": "us-east-1",
        "tf_working_dir": "envs/x",
    }
    b.update(over)
    return b


async def test_invalid_state_backend_value_422(auth_client, admin_token, default_aws_account):
    r = await auth_client.post(
        "/api/v1/workspaces",
        json=_ws_body(default_aws_account, name="ws-bad", tf_working_dir="envs/bad", state_backend="swift"),
        headers=_h(admin_token),
    )
    assert r.status_code == 422


async def test_azureblob_requires_configured_sub(auth_client, admin_token, default_aws_account):
    # azureblob with no azure link at all → 422
    r = await auth_client.post(
        "/api/v1/workspaces",
        json=_ws_body(default_aws_account, name="ws-az0", tf_working_dir="envs/az0", state_backend="azureblob"),
        headers=_h(admin_token),
    )
    assert r.status_code == 422
    assert "azureblob" in r.json()["detail"]

    # linked sub exists but has NO storage container → still 422
    sub_id = await _mk_azure_sub(auth_client, admin_token, with_storage=False)
    r = await auth_client.post(
        "/api/v1/workspaces",
        json=_ws_body(
            default_aws_account, name="ws-az1", tf_working_dir="envs/az1",
            azure_subscription_id=sub_id, state_backend="azureblob",
        ),
        headers=_h(admin_token),
    )
    assert r.status_code == 422


async def test_azureblob_happy_path(auth_client, admin_token, default_aws_account):
    sub_id = await _mk_azure_sub(auth_client, admin_token, with_storage=True)
    r = await auth_client.post(
        "/api/v1/workspaces",
        json=_ws_body(
            default_aws_account, name="ws-azok", tf_working_dir="envs/azok",
            azure_subscription_id=sub_id, state_backend="azureblob",
        ),
        headers=_h(admin_token),
    )
    assert r.status_code == 201, r.text
    assert r.json()["state_backend"] == "azureblob"


async def test_gcs_requires_bucket(auth_client, admin_token, default_aws_account):
    # gcs with no gcp link → 422
    r = await auth_client.post(
        "/api/v1/workspaces",
        json=_ws_body(default_aws_account, name="ws-gcs0", tf_working_dir="envs/gcs0", state_backend="gcs"),
        headers=_h(admin_token),
    )
    assert r.status_code == 422

    # project WITHOUT a bucket → 422
    proj = await _mk_gcp_project(auth_client, admin_token, "gcsproj-nobkt", with_bucket=False)
    r = await auth_client.post(
        "/api/v1/workspaces",
        json=_ws_body(
            default_aws_account, name="ws-gcs1", tf_working_dir="envs/gcs1",
            gcp_project_id=proj, state_backend="gcs",
        ),
        headers=_h(admin_token),
    )
    assert r.status_code == 422


async def test_gcs_update_path_happy(auth_client, admin_token, default_aws_account):
    proj = await _mk_gcp_project(auth_client, admin_token, "gcsproj-ok", with_bucket=True)
    created = await auth_client.post(
        "/api/v1/workspaces",
        json=_ws_body(default_aws_account, name="ws-up", tf_working_dir="envs/up"),
        headers=_h(admin_token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["state_backend"] == "s3"
    ws_id = created.json()["id"]

    # Flip to gcs + link project in the same call → effective-linkage check passes.
    upd = await auth_client.put(
        f"/api/v1/workspaces/{ws_id}",
        json={"state_backend": "gcs", "gcp_project_id": proj},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["state_backend"] == "gcs"
    assert upd.json()["gcp_project_id"] == proj
