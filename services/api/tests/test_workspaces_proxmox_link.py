"""Workspace ↔ Proxmox cluster linkage: create/update validation and the
bulk-import path auto-link for `proxmox/cluster-<slug>/<node>/<stack>`."""
import pytest

from app.models.business_unit import DEFAULT_BU_ID
from app.models.proxmox_cluster import ProxmoxCluster
from app.services import proxmox_cluster_service as svc

pytestmark = pytest.mark.usefixtures("default_bu", "default_aws_account")


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


async def _cluster(_setup_db, slug="home", bu=DEFAULT_BU_ID):
    async with _setup_db() as s:
        row = ProxmoxCluster(
            business_unit_id=bu, slug=slug, name=slug, endpoint="https://pve:8006",
            api_token_id="u@pam!t", api_token_secret_encrypted=svc.encrypt_secret("x"),
        )
        s.add(row)
        await s.commit()
        return row.id


def test_slug_from_path():
    from app.routers.workspaces import _proxmox_slug_from_path as f

    assert f("proxmox/cluster-home/pve/vm-web") == "home"
    assert f("proxmox/cluster-home-lab2/pve2/lxc/dns") == "home-lab2"
    assert f("PROXMOX/cluster-home/pve/x") == "home"
    assert f("proxmox/home/pve/x") is None
    assert f("gcp/project-p1/us/x") is None
    assert f("") is None


async def test_import_auto_links_registered_cluster(auth_client, admin_token, _setup_db):
    await _cluster(_setup_db, slug="home")
    body = {
        "repo_url": "https://example.com/infra.git", "ref": "main",
        "entries": [
            {"path": "proxmox/cluster-home/pve/vm-web", "name": "vm-web",
             "aws_account_id": "global", "region": "global", "environment": "prod"},
            {"path": "proxmox/cluster-unknown/pve/vm-db", "name": "vm-db",
             "aws_account_id": "global", "region": "global", "environment": "prod"},
        ],
    }
    r = await auth_client.post("/api/v1/workspaces/import", json=body, headers=_h(admin_token))
    assert r.status_code == 201, r.text
    created = {w["name"]: w for w in r.json()["created"]}
    assert created["vm-web"]["proxmox_cluster_id"] is not None
    assert created["vm-web"]["state_backend"] == "s3"
    assert created["vm-db"]["proxmox_cluster_id"] is None


async def test_create_rejects_cluster_from_other_bu(auth_client, admin_token, _setup_db, default_aws_account):
    from app.models.business_unit import BusinessUnit

    async with _setup_db() as s:
        s.add(BusinessUnit(id="bu-x", slug="x", name="X"))
        await s.commit()
    other = await _cluster(_setup_db, slug="theirs", bu="bu-x")
    body = {
        "name": "vm", "environment": "dev", "aws_account_id": default_aws_account, "region": "us-east-1",
        "repo_url": "local://", "tf_working_dir": "proxmox/cluster-theirs/pve/vm",
        "proxmox_cluster_id": other,
    }
    r = await auth_client.post("/api/v1/workspaces", json=body, headers=_h(admin_token))
    assert r.status_code == 400
    assert "Proxmox cluster" in r.text


async def test_update_sets_and_clears_link(auth_client, admin_token, _setup_db, default_aws_account):
    mine = await _cluster(_setup_db, slug="mine")
    body = {
        "name": "vm2", "environment": "dev", "aws_account_id": default_aws_account, "region": "us-east-1",
        "repo_url": "local://", "tf_working_dir": "proxmox/cluster-mine/pve/vm2",
    }
    r = await auth_client.post("/api/v1/workspaces", json=body, headers=_h(admin_token))
    assert r.status_code == 201, r.text
    ws_id = r.json()["id"]
    r = await auth_client.put(f"/api/v1/workspaces/{ws_id}", json={"proxmox_cluster_id": mine}, headers=_h(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["proxmox_cluster_id"] == mine
    r = await auth_client.put(f"/api/v1/workspaces/{ws_id}", json={"proxmox_cluster_id": ""}, headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["proxmox_cluster_id"] is None
    r = await auth_client.put(f"/api/v1/workspaces/{ws_id}", json={"proxmox_cluster_id": "nope"}, headers=_h(admin_token))
    assert r.status_code == 422
