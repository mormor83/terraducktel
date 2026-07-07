"""Tests for repo discovery (account-XXX/region/leaf detection)."""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

import os
import uuid

import pytest


def _scaffold_infra(root: str) -> None:
    """Build a fixture directory mirroring the account/region/leaf infra layout."""
    leaves = [
        "account-111111111111/eu-central-1/region-shared-resources",
        "account-111111111111/eu-central-1/superset",
        "account-111111111111/us-east-1/cust06",
        "account-111111111111/us-east-1/staging",
        "account-333333333333/us-east-1/prod",
        "account-333333333333/us-east-1/preprod",
        "account-333333333333/eu-west-1/monitoring",
        # NEGATIVE CASES (should NOT be picked up):
        "not-an-account/us-east-1/foo",          # bad account dir
        "account-1234/not-a-region/foo",          # bad region
        "account-1234/us-east-1/foo/nested-deep", # depth >3
    ]
    for leaf in leaves:
        d = os.path.join(root, leaf)
        os.makedirs(d, exist_ok=True)
        # Drop a main.tf so the walker considers this a TF stack
        with open(os.path.join(d, "main.tf"), "w") as f:
            f.write("# fixture\n")


def test_discover_local_finds_only_3_segment_leaves(tmp_path):
    from app.services.repo_discovery import discover_local

    _scaffold_infra(str(tmp_path))
    result = discover_local(str(tmp_path), repo_url="https://example.com/x.git", ref="main")

    paths = sorted(s.path for s in result.stacks)
    assert paths == sorted([
        "account-111111111111/eu-central-1/region-shared-resources",
        "account-111111111111/eu-central-1/superset",
        "account-111111111111/us-east-1/cust06",
        "account-111111111111/us-east-1/staging",
        "account-333333333333/eu-west-1/monitoring",
        "account-333333333333/us-east-1/preprod",
        "account-333333333333/us-east-1/prod",
    ])
    # Negative-case bad-account/bad-region/depth-4 should be skipped.
    assert all("not-an-account" not in p for p in paths)
    assert all("not-a-region" not in p for p in paths)
    assert all("nested-deep" not in p for p in paths)


def test_discover_local_picks_up_non_aws_flat_layouts(tmp_path):
    """Generic / non-AWS provider folders (cloudflare/foo, azure/foo) are
    now picked up by discovery and given sentinel ('global', 'global')
    values for aws_account_id + region. Malformed AWS-shaped paths
    (`account-1234/...`, `not-an-account/us-east-1/...`) remain rejected
    — they're typos, not generic layouts."""
    import os

    from app.services.repo_discovery import discover_local

    leaves = [
        # AWS-shaped — still picked up unchanged.
        "account-111111111111/eu-central-1/superset",
        # Non-AWS flat layouts — should now be picked up as generic.
        "cloudflare/tenant-home-bu",
        "azure/rg-shared",
        # Typo'd AWS paths — should still be rejected, not generic-ified.
        "account-1234/us-east-1/foo",         # account regex fails
        "not-an-account/us-east-1/bar",       # parts[1] is a region → was AWS intent
    ]
    for leaf in leaves:
        d = os.path.join(str(tmp_path), leaf)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "main.tf"), "w") as f:
            f.write("# fixture\n")

    result = discover_local(str(tmp_path))
    by_path = {s.path: s for s in result.stacks}

    # AWS still works.
    aws = by_path["account-111111111111/eu-central-1/superset"]
    assert aws.aws_account_id == "111111111111"
    assert aws.region == "eu-central-1"

    # Cloudflare + Azure pick up the sentinels.
    cf = by_path["cloudflare/tenant-home-bu"]
    assert cf.aws_account_id == "global"
    assert cf.region == "global"
    assert cf.name == "tenant-home-bu"
    assert cf.state_key.startswith("global/global/")

    az = by_path["azure/rg-shared"]
    assert az.aws_account_id == "global"
    assert az.region == "global"

    # Typo'd AWS paths stay out of the results.
    assert "account-1234/us-east-1/foo" not in by_path
    assert "not-an-account/us-east-1/bar" not in by_path


def test_environment_suggestion_matches_path_keywords():
    from app.services.repo_discovery import suggest_environment

    assert suggest_environment("prod") == "prod"
    assert suggest_environment("preprod") == "preprod"
    assert suggest_environment("staging") == "staging"
    assert suggest_environment("region-shared-resources") == "shared"
    assert suggest_environment("monitoring") == "shared"
    assert suggest_environment("cust06") == "prod"
    assert suggest_environment("cust01") == "prod"
    assert suggest_environment("rc") == "prod"
    assert suggest_environment("preset") == "prod"
    # Unknown leaves default to dev (admin can override during import).
    assert suggest_environment("totally-new-thing-xyz") == "dev"


def test_discover_groups_accounts_and_regions(tmp_path):
    from app.services.repo_discovery import discover_local

    _scaffold_infra(str(tmp_path))
    result = discover_local(str(tmp_path))

    by_acc = {a.aws_account_id: a for a in result.accounts}
    assert "111111111111" in by_acc
    assert "333333333333" in by_acc
    assert sorted(by_acc["333333333333"].regions.keys()) == ["eu-west-1", "us-east-1"]
    us_east = by_acc["333333333333"].regions["us-east-1"]
    assert {s.name for s in us_east} == {"prod", "preprod"}


@pytest.mark.asyncio
async def test_bulk_import_creates_workspaces_with_isolated_state_paths(
    auth_client, seeded_users, _setup_db
):
    """Each leaf gets its own state_path under tfstate/{acct}/{region}/{env}/{name}."""
    # Login as admin.
    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    # Two stacks with the SAME leaf name in different (account, region) — must NOT collide.
    body = {
        "repo_url": "https://example.com/infra.git",
        "ref": "main",
        "entries": [
            {
                "path": "account-A/us-east-1/monitoring",
                "name": "monitoring",
                "aws_account_id": "111111111111",
                "region": "us-east-1",
                "environment": "shared",
            },
            {
                "path": "account-B/eu-central-1/monitoring",
                "name": "monitoring",
                "aws_account_id": "222222222222",
                "region": "eu-central-1",
                "environment": "shared",
            },
        ],
    }
    r = await auth_client.post(
        "/api/v1/workspaces/import",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    created = r.json()["created"]
    assert len(created) == 2
    # tf_working_dir reflects the repo path; state isolation is at the (account, region, env, name) level.
    paths = {w["tf_working_dir"] for w in created}
    assert paths == {
        "account-A/us-east-1/monitoring",
        "account-B/eu-central-1/monitoring",
    }

    # Re-importing the same set should be idempotent (skipped, not error).
    r2 = await auth_client.post(
        "/api/v1/workspaces/import",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 201
    assert r2.json()["created"] == []
    assert len(r2.json()["skipped"]) == 2


def test_inject_credentials_url_encodes_and_only_for_http():
    from app.services.repo_discovery import _inject_credentials, _redact_url

    # Special chars in token must be percent-encoded so the URL still parses.
    url = _inject_credentials("https://github.com/a/b.git", "alice", "tok:en/with@chars")
    assert url == "https://alice:tok%3Aen%2Fwith%40chars@github.com/a/b.git"

    # ssh URLs are returned unchanged.
    ssh = "git@github.com:a/b.git"
    assert _inject_credentials(ssh, "alice", "tok") == ssh

    # Redaction strips embedded creds.
    assert _redact_url(url) == "https://github.com/a/b.git"


def test_local_path_discovery_requires_env_root(tmp_path, monkeypatch):
    from app.services import repo_discovery

    monkeypatch.delenv("TERRADUCKTEL_LOCAL_REPOS_DIR", raising=False)
    result = repo_discovery.discover_local_path(str(tmp_path))
    assert result.errors and "TERRADUCKTEL_LOCAL_REPOS_DIR" in result.errors[0]
    assert result.stacks == []


def test_local_path_discovery_rejects_paths_outside_root(tmp_path, monkeypatch):
    from app.services import repo_discovery

    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    monkeypatch.setenv("TERRADUCKTEL_LOCAL_REPOS_DIR", str(safe_root))
    result = repo_discovery.discover_local_path(str(outside))
    assert result.errors and "outside TERRADUCKTEL_LOCAL_REPOS_DIR" in result.errors[0]


def test_local_path_discovery_walks_under_root(tmp_path, monkeypatch):
    from app.services import repo_discovery

    monkeypatch.setenv("TERRADUCKTEL_LOCAL_REPOS_DIR", str(tmp_path))
    repo = tmp_path / "infra"
    leaf = repo / "account-111111111111" / "us-east-1" / "monitoring"
    leaf.mkdir(parents=True)
    (leaf / "main.tf").write_text("# fixture\n")

    result = repo_discovery.discover_local_path(str(repo))
    assert result.errors == []
    assert {s.path for s in result.stacks} == {"account-111111111111/us-east-1/monitoring"}
    # Repo URL is the local:// scheme so the UI can distinguish.
    assert result.repo_url.startswith("local://")


def test_workspace_state_path_now_includes_region():
    from app.models.workspace import Workspace

    ws = Workspace(
        id=str(uuid.uuid4()),
        name="monitoring",
        aws_account_id="111111111111",
        region="us-east-1",
        environment="shared",
        tf_working_dir="account-A/us-east-1/monitoring",
    )
    assert ws.state_path == "tfstate/111111111111/us-east-1/shared/monitoring/terraform.tfstate"

    ws2 = Workspace(
        id=str(uuid.uuid4()),
        name="monitoring",
        aws_account_id="222222222222",
        region="eu-central-1",
        environment="shared",
        tf_working_dir="account-B/eu-central-1/monitoring",
    )
    assert ws2.state_path == "tfstate/222222222222/eu-central-1/shared/monitoring/terraform.tfstate"
    assert ws.state_path != ws2.state_path  # Per-region isolation.


async def test_bulk_import_auto_links_azure_subscription(auth_client, _setup_db):
    """Azure leaves auto-link their subscription from the path; unregistered GUIDs stay unlinked."""
    from app.models.azure_subscription import AzureSubscription
    from app.models.business_unit import DEFAULT_BU_ID

    token = (
        await auth_client.post(
            "/api/v1/auth/token", json={"email": "admin@test.com", "password": "password123"}
        )
    ).json()["access_token"]

    guid = "da59de93-a478-420e-b3e3-28609e47237b"
    async with _setup_db() as s:
        s.add(AzureSubscription(
            business_unit_id=DEFAULT_BU_ID, subscription_id=guid,
            tenant_id="t", client_id="c", client_secret_encrypted="x", name="Acme-Home-Dev",
        ))
        await s.commit()

    body = {
        "repo_url": "https://example.com/infra.git", "ref": "main",
        "entries": [
            {"path": f"azure/subscription-{guid}/eastus/foundry", "name": "foundry",
             "aws_account_id": "global", "region": "global", "environment": "prod"},
            # valid GUID shape but not registered in this BU → stays unlinked (no error)
            {"path": "azure/subscription-11111111-1111-1111-1111-111111111111/eastus/x", "name": "x",
             "aws_account_id": "global", "region": "global", "environment": "prod"},
        ],
    }
    r = await auth_client.post(
        "/api/v1/workspaces/import", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 201, r.text
    created = {w["name"]: w for w in r.json()["created"]}
    assert created["foundry"]["azure_subscription_id"] is not None  # auto-linked from path
    assert created["x"]["azure_subscription_id"] is None             # unregistered → unlinked
