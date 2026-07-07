"""Firefly-style cloud asset inventory API."""
import pytest

pytestmark = pytest.mark.usefixtures("default_aws_account")

from unittest.mock import AsyncMock, patch

import pytest_asyncio


@pytest_asyncio.fixture
async def workspace_id(auth_client, admin_token):
    create = await auth_client.post(
        "/api/v1/workspaces",
        json={
            "name": "inv-ws",
            "environment": "dev",
            "aws_account_id": "123456789012",
            "region": "us-east-1",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 201
    return create.json()["id"]


_ASSETS = [
    {"asset_id": "arn:aws:ec2:us-east-1:123:instance/i-managed", "address": "aws_instance.web",
     "asset_type": "aws_instance", "provider": "aws", "region": "us-east-1",
     "account_id": "123456789012", "iac_status": "codified"},
    {"asset_id": "arn:aws:ec2:us-east-1:123:instance/i-drift", "address": "aws_instance.api",
     "asset_type": "aws_instance", "provider": "aws", "region": "us-east-1",
     "account_id": "123456789012", "iac_status": "drifted", "drift_summary": "type changed"},
    {"asset_id": "arn:aws:s3:::rogue", "address": "", "asset_type": "s3", "provider": "aws",
     "region": "us-east-1", "account_id": "123456789012", "iac_status": "unmanaged"},
    {"asset_id": "arn:aws:iam::123:role/stale", "address": "aws_iam_role.stale",
     "asset_type": "aws_iam_role", "provider": "aws", "region": "us-east-1",
     "account_id": "123456789012", "iac_status": "ghost"},
]


async def _post_assets(auth_client, admin_token, workspace_id, assets=_ASSETS):
    with patch("app.routers.drift.send_drift_alert", new_callable=AsyncMock):
        r = await auth_client.post(
            f"/api/v1/drift/{workspace_id}/report",
            json={"workspace_id": workspace_id, "has_drift": True, "summary": "scan",
                  "plan_output": "", "assets": assets},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert r.status_code == 200, r.text


_MIXED = [
    {"asset_id": "arn:aws:s3:::a", "address": "aws_s3_bucket.a", "asset_type": "aws_s3_bucket",
     "provider": "aws", "region": "us-east-1", "account_id": "111", "iac_status": "codified"},
    {"asset_id": "arn:aws:s3:::rogue", "address": "", "asset_type": "s3",
     "provider": "aws", "region": "us-east-1", "account_id": "111", "iac_status": "unmanaged"},
    {"asset_id": "/subscriptions/x/resourceGroups/rg", "address": "azurerm_resource_group.r",
     "asset_type": "azurerm_resource_group", "provider": "azurerm", "region": "eastus",
     "account_id": "222", "iac_status": "codified"},
]


async def test_summary_respects_scope_filters(auth_client, admin_token, workspace_id):
    """Provider/region/account/search rescope the KPI cards; facets stay global."""
    await _post_assets(auth_client, admin_token, workspace_id, assets=_MIXED)
    hdr = {"Authorization": f"Bearer {admin_token}"}

    full = (await auth_client.get("/api/v1/inventory/summary", headers=hdr)).json()
    assert full["total"] == 3
    assert set(full["facets"]["providers"]) == {"aws", "azurerm"}  # azure shows up

    aws = (await auth_client.get("/api/v1/inventory/summary", params={"provider": "aws"}, headers=hdr)).json()
    assert aws["total"] == 2 and aws["counts"]["codified"] == 1 and aws["counts"]["unmanaged"] == 1
    # facets remain BU-global even when scoped, so the dropdowns never empty out
    assert set(aws["facets"]["providers"]) == {"aws", "azurerm"}

    az = (await auth_client.get("/api/v1/inventory/summary", params={"provider": "azurerm"}, headers=hdr)).json()
    assert az["total"] == 1 and az["counts"]["codified"] == 1 and az["codification_pct"] == 100

    acct = (await auth_client.get("/api/v1/inventory/summary", params={"account_id": "222"}, headers=hdr)).json()
    assert acct["total"] == 1


async def test_inventory_summary_codification(auth_client, admin_token, workspace_id):
    await _post_assets(auth_client, admin_token, workspace_id)
    r = await auth_client.get(
        "/api/v1/inventory/summary", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    assert body["counts"]["codified"] == 1
    assert body["counts"]["drifted"] == 1
    assert body["counts"]["unmanaged"] == 1
    assert body["counts"]["ghost"] == 1
    # tracked (codified+drifted+ghost)=3 of 4 → 75%
    assert body["codification_pct"] == 75
    assert "us-east-1" in body["facets"]["regions"]


async def test_inventory_assets_filter_by_status(auth_client, admin_token, workspace_id):
    await _post_assets(auth_client, admin_token, workspace_id)
    r = await auth_client.get(
        "/api/v1/inventory/assets",
        params={"iac_status": "unmanaged"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["iac_status"] == "unmanaged"
    assert body["items"][0]["asset_id"] == "arn:aws:s3:::rogue"


async def test_inventory_assets_search(auth_client, admin_token, workspace_id):
    await _post_assets(auth_client, admin_token, workspace_id)
    r = await auth_client.get(
        "/api/v1/inventory/assets",
        params={"search": "aws_instance.web"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 1


async def test_rescan_replaces_workspace_assets(auth_client, admin_token, workspace_id):
    """A second report for the same workspace replaces, not duplicates."""
    await _post_assets(auth_client, admin_token, workspace_id)
    # second scan: the drifted instance is now codified, ghost is gone
    second = [
        {**_ASSETS[0]},
        {**_ASSETS[1], "iac_status": "codified", "drift_summary": ""},
    ]
    await _post_assets(auth_client, admin_token, workspace_id, assets=second)
    r = await auth_client.get(
        "/api/v1/inventory/summary", headers={"Authorization": f"Bearer {admin_token}"}
    )
    body = r.json()
    # ghost row and the rogue unmanaged were not re-reported → pruned for this ws/account
    assert body["counts"]["codified"] == 2
    assert body["counts"]["drifted"] == 0
    assert body["counts"]["ghost"] == 0


async def test_inventory_excludes_other_bu(auth_client, admin_token, workspace_id, _setup_db):
    await _post_assets(auth_client, admin_token, workspace_id)

    from app.models.business_unit import BusinessUnit
    from app.models.cloud_asset import CloudAsset

    async with _setup_db() as session:
        session.add(BusinessUnit(id="other-bu", slug="other-bu", name="Other"))
        session.add(
            CloudAsset(
                business_unit_id="other-bu",
                asset_id="arn:aws:s3:::other-bu-secret",
                asset_type="s3",
                iac_status="unmanaged",
                region="us-east-1",
                account_id="999",
            )
        )
        await session.commit()

    r = await auth_client.get(
        "/api/v1/inventory/assets", headers={"Authorization": f"Bearer {admin_token}"}
    )
    ids = {a["asset_id"] for a in r.json()["items"]}
    assert "arn:aws:s3:::other-bu-secret" not in ids
    assert "arn:aws:s3:::rogue" in ids


# ─── service-managed + ignore rules ──────────────────────────────────────────

_SVC = [
    {"asset_id": "arn:cod", "address": "aws_x.a", "asset_type": "aws_x", "provider": "aws",
     "region": "us-east-1", "account_id": "1", "iac_status": "codified"},
    {"asset_id": "arn:rogue", "asset_type": "s3", "provider": "aws",
     "region": "us-east-1", "account_id": "1", "iac_status": "unmanaged"},
    {"asset_id": "arn:aws:ec2:::fleet/f1", "asset_type": "ec2", "provider": "aws",
     "region": "us-east-1", "account_id": "1", "iac_status": "service_managed",
     "drift_summary": "managed by EKS"},
]


async def test_service_managed_excluded_from_codification(auth_client, admin_token, workspace_id):
    await _post_assets(auth_client, admin_token, workspace_id, assets=_SVC)
    body = (await auth_client.get(
        "/api/v1/inventory/summary", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    assert body["counts"]["service_managed"] == 1
    assert body["counts"]["codified"] == 1 and body["counts"]["unmanaged"] == 1
    # base = total(3) - ignored(0) - service_managed(1) = 2; tracked(codified)=1 → 50%
    assert body["codification_pct"] == 50


async def test_ignore_rule_reclassifies_existing_and_at_ingest(auth_client, admin_token, workspace_id):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    # existing unmanaged asset
    await _post_assets(auth_client, admin_token, workspace_id, assets=[
        {"asset_id": "arn:aws:logs:us-east-1:1:log-group:/aws/foo", "asset_type": "aws_cloudwatch_log_group",
         "provider": "aws", "region": "us-east-1", "account_id": "1", "iac_status": "unmanaged"},
    ])
    # create an arn_glob rule → should reclassify the existing asset immediately
    r = await auth_client.post(
        "/api/v1/inventory/ignore-rules",
        json={"match_type": "arn_glob", "pattern": "arn:aws:logs:*", "note": "log groups"},
        headers=hdr,
    )
    assert r.status_code == 201, r.text
    rule_id = r.json()["id"]
    ignored = (await auth_client.get(
        "/api/v1/inventory/assets", params={"iac_status": "ignored"}, headers=hdr)).json()
    assert ignored["total"] == 1
    # list shows the rule
    assert len(((await auth_client.get("/api/v1/inventory/ignore-rules", headers=hdr)).json())) == 1
    # ingest-time: a NEW matching unmanaged asset comes in already ignored
    await _post_assets(auth_client, admin_token, workspace_id, assets=[
        {"asset_id": "arn:aws:logs:us-east-1:1:log-group:/aws/bar", "asset_type": "aws_cloudwatch_log_group",
         "provider": "aws", "region": "us-east-1", "account_id": "1", "iac_status": "unmanaged"},
    ])
    s = (await auth_client.get("/api/v1/inventory/summary", headers=hdr)).json()
    assert s["counts"]["ignored"] == 1 and s["counts"]["unmanaged"] == 0
    # delete the rule
    d = await auth_client.delete(f"/api/v1/inventory/ignore-rules/{rule_id}", headers=hdr)
    assert d.status_code == 204
    assert (await auth_client.get("/api/v1/inventory/ignore-rules", headers=hdr)).json() == []


async def test_ignore_rule_rejects_bad_match_type(auth_client, admin_token):
    r = await auth_client.post(
        "/api/v1/inventory/ignore-rules",
        json={"match_type": "nonsense", "pattern": "x"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 422


async def test_duplicate_managed_asset_id_across_workspaces_does_not_500(
    auth_client, admin_token, workspace_id, _setup_db
):
    """Two workspaces in the same BU reporting a managed asset with the SAME
    asset_id must not 500 the second report.

    Regression for the sample-backend-deps bug: random_password's id=="none" gave
    many resources asset_id="none", colliding on the (business_unit_id,
    asset_id) unique key. The collision used to abort the whole report (500),
    silently dropping every asset in the workspace so its real resources showed
    as "unmanaged". The detector now strips such logical resources, and ingest
    skips a managed asset_id already owned by a sibling rather than aborting.
    """
    hdr = {"Authorization": f"Bearer {admin_token}"}
    # A second workspace in the same (default) BU. Distinct tf_working_dir so it
    # doesn't collide with the `workspace_id` fixture on the per-BU identity
    # tuple (account, region, environment, path); the inventory dedup under test
    # is keyed on (business_unit_id, asset_id), independent of the path.
    ws2 = await auth_client.post(
        "/api/v1/workspaces",
        json={"name": "inv-ws-2", "environment": "dev",
              "aws_account_id": "123456789012", "region": "us-east-1",
              "tf_working_dir": "envs/inv-ws-2"},
        headers=hdr,
    )
    assert ws2.status_code == 201, ws2.text
    ws2_id = ws2.json()["id"]

    dup = lambda addr: [{  # noqa: E731
        "asset_id": "none", "address": addr, "asset_type": "random_password",
        "provider": "random", "region": "us-east-1", "account_id": "123456789012",
        "iac_status": "codified",
    }]
    # First workspace claims asset_id "none".
    await _post_assets(auth_client, admin_token, workspace_id, assets=dup("random_password.a"))
    # Second workspace reports the SAME asset_id — must still succeed (200).
    await _post_assets(auth_client, admin_token, ws2_id, assets=dup("random_password.b"))

    # Exactly one row persisted for that asset_id; no 500, nothing lost.
    from sqlalchemy import select, func
    from app.models.cloud_asset import CloudAsset
    async with _setup_db() as session:
        n = (await session.execute(
            select(func.count()).select_from(CloudAsset).where(CloudAsset.asset_id == "none")
        )).scalar()
    assert n == 1
