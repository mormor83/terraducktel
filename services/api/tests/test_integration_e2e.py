"""Stack integration tests (Phase 8) — API workflow without real Terraform executor."""
import pytest


@pytest.mark.integration
async def test_workspace_run_approve_audit_trail(
    auth_client, admin_token, operator_token, default_aws_account
):
    """Create workspace → run → awaiting approval → approve → audit contains approve."""
    admin_h = {"Authorization": f"Bearer {admin_token}", "X-Business-Unit": "default"}
    op_h = {"Authorization": f"Bearer {operator_token}", "X-Business-Unit": "default"}
    ws = await auth_client.post(
        "/api/v1/workspaces",
        json={
            "name": "e2e-integration-ws",
            "environment": "dev",
            "aws_account_id": "123456789012",
            "region": "us-east-1",
        },
        headers=admin_h,
    )
    assert ws.status_code == 201, ws.text
    ws_id = ws.json()["id"]

    run_resp = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers=op_h,
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    for status in ("running", "planning", "planned", "awaiting_approval"):
        payload: dict = {"status": status}
        if status == "awaiting_approval":
            payload["plan_output"] = "e2e plan"
        pr = await auth_client.patch(
            f"/api/v1/runs/{run_id}",
            json=payload,
            headers=op_h,
        )
        assert pr.status_code == 200, pr.text

    ap = await auth_client.post(
        f"/api/v1/runs/{run_id}/approve",
        json={"comment": "integration"},
        headers=admin_h,
    )
    assert ap.status_code == 200

    audit = await auth_client.get(
        f"/api/v1/audit?run_id={run_id}",
        headers=admin_h,
    )
    assert audit.status_code == 200
    actions = [e["action"] for e in audit.json()["items"]]
    assert "approve" in actions


@pytest.mark.integration
async def test_workspace_list_excludes_jwt_secret(auth_client, admin_token, default_bu):
    """JWT secret never appears in workspace list payload."""
    import os

    secret = os.environ.get("JWT_SECRET_KEY", "")
    r = await auth_client.get(
        "/api/v1/workspaces",
        headers={"Authorization": f"Bearer {admin_token}", "X-Business-Unit": "default"},
    )
    assert r.status_code == 200
    assert secret not in r.text
