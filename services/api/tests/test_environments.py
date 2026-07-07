"""Tests for environment promotion router."""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID



class TestEnvironmentPromotion:
    async def test_list_environments(self, auth_client, admin_token, seeded_users):
        headers = {"Authorization": f"Bearer {admin_token}"}
        ws = await auth_client.post(
            "/api/v1/workspaces",
            json={"name": "promo-test", "environment": "dev", "aws_account_id": "000000000000"},
            headers=headers,
        )
        assert ws.status_code == 201

        r = await auth_client.get("/api/v1/environments", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert "environments" in data
        assert "promotion_order" in data
        assert data["promotion_order"] == ["dev", "staging", "prod"]

    async def test_promote_dev_to_staging(self, auth_client, admin_token, seeded_users):
        headers = {"Authorization": f"Bearer {admin_token}"}
        ws = await auth_client.post(
            "/api/v1/workspaces",
            json={"name": "promo-ws", "environment": "dev", "aws_account_id": "000000000000"},
            headers=headers,
        )
        ws_id = ws.json()["id"]

        r = await auth_client.post(f"/api/v1/environments/{ws_id}/promote", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["promoted_from"] == "dev"
        assert data["promoted_to"] == "staging"
        assert "run_id" in data

    async def test_promote_carries_repo_ref_and_config(
        self, auth_client, admin_token, seeded_users, _setup_db
    ):
        """The promoted workspace must mirror the SOURCE's tracked branch +
        cloud config — not silently fall back to the model defaults (repo_ref
        → 'main'), which would plan/apply the wrong code in the next stage."""
        from app.models.workspace import Workspace

        headers = {"Authorization": f"Bearer {admin_token}"}
        ws = await auth_client.post(
            "/api/v1/workspaces",
            json={
                "name": "ref-ws",
                "environment": "dev",
                "aws_account_id": "000000000000",
                "repo_url": "https://github.com/o/r.git",
            },
            headers=headers,
        )
        ws_id = ws.json()["id"]
        # Source tracks a non-default branch.
        async with _setup_db() as s:
            row = await s.get(Workspace, ws_id)
            row.repo_ref = "release/v2"
            row.webhook_enabled = True
            # dev-specific S3 state key — must NOT carry over to staging.
            row.state_key = "000000000000/us-east-1/dev/ref-ws"
            await s.commit()

        r = await auth_client.post(f"/api/v1/environments/{ws_id}/promote", headers=headers)
        assert r.status_code == 200
        target_id = r.json()["target_workspace_id"]

        async with _setup_db() as s:
            promoted = await s.get(Workspace, target_id)
            assert promoted.repo_ref == "release/v2"  # NOT the "main" default
            assert promoted.repo_url == "https://github.com/o/r.git"
            assert promoted.business_unit_id == DEFAULT_BU_ID
            assert promoted.environment == "staging"
            # Intentionally reset / derived fresh for the new env:
            assert promoted.state_key is None  # would alias dev's tfstate if copied
            assert promoted.webhook_enabled is False  # no auto-trigger until opt-in
            assert promoted.drift_status == "unknown"

    async def test_promote_missing_workspace_404(self, auth_client, admin_token, seeded_users):
        headers = {"Authorization": f"Bearer {admin_token}"}
        r = await auth_client.post("/api/v1/environments/ghost/promote", headers=headers)
        assert r.status_code == 404

    async def test_promote_env_not_in_chain_400(
        self, auth_client, admin_token, seeded_users, _setup_db
    ):
        from app.models.workspace import Workspace

        headers = {"Authorization": f"Bearer {admin_token}"}
        ws = await auth_client.post(
            "/api/v1/workspaces",
            json={"name": "shared-ws", "environment": "dev", "aws_account_id": "000000000000"},
            headers=headers,
        )
        ws_id = ws.json()["id"]
        async with _setup_db() as s:
            row = await s.get(Workspace, ws_id)
            row.environment = "shared"  # not in dev/staging/prod chain
            await s.commit()
        r = await auth_client.post(f"/api/v1/environments/{ws_id}/promote", headers=headers)
        assert r.status_code == 400

    async def test_promote_prod_fails(self, auth_client, admin_token, seeded_users):
        headers = {"Authorization": f"Bearer {admin_token}"}
        ws = await auth_client.post(
            "/api/v1/workspaces",
            json={"name": "prod-ws", "environment": "prod", "aws_account_id": "000000000000"},
            headers=headers,
        )
        ws_id = ws.json()["id"]

        r = await auth_client.post(f"/api/v1/environments/{ws_id}/promote", headers=headers)
        assert r.status_code == 409


class TestMetricsEndpoint:
    async def test_metrics_returns_prometheus_format(self, auth_client):
        r = await auth_client.get("/metrics")
        assert r.status_code == 200
        assert "http_requests_total" in r.text
