"""TDD RED tests for Orchestration API: auth, RBAC, CRUD, approvals.

These tests verify:
1. JWT authentication (401 for missing/invalid tokens)
2. RBAC enforcement (role hierarchy: viewer < operator < admin)
3. Workspace CRUD with role gating
4. Run triggering and listing
5. Approval 4-eyes rule (triggerer cannot approve own run)
6. User listing (admin only)

Fixtures: tests/conftest.py (auth_client, tokens, seeded_users).
"""
import pytest

# These tests predate Business Units and don't send an X-Business-Unit header,
# so they rely on `current_bu` resolving to the caller's single membership.
# `default_aws_account` seeds the default BU + memberships AND the common test
# AWS accounts (workspace creation now requires a registered account).
pytestmark = pytest.mark.usefixtures("default_aws_account")


# ---------------------------------------------------------------------------
# 1. Authentication tests
# ---------------------------------------------------------------------------

async def test_unauthenticated_request_returns_401(auth_client):
    """No token -> 401."""
    response = await auth_client.get("/api/v1/workspaces")
    assert response.status_code == 401


async def test_invalid_token_returns_401(auth_client):
    """Bad JWT -> 401."""
    response = await auth_client.get(
        "/api/v1/workspaces",
        headers={"Authorization": "Bearer invalid.jwt.token"},
    )
    assert response.status_code == 401


async def test_login_returns_access_and_refresh_tokens(auth_client, seeded_users):
    """POST /auth/token returns both access_token and refresh_token."""
    resp = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"


async def test_login_wrong_password_returns_401(auth_client, seeded_users):
    """Wrong password -> 401."""
    resp = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "wrong"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. RBAC tests (role hierarchy enforcement)
# ---------------------------------------------------------------------------

async def test_viewer_cannot_create_workspace(auth_client, viewer_token):
    """Viewer role must be rejected for POST /workspaces (requires admin)."""
    response = await auth_client.post(
        "/api/v1/workspaces",
        json={"name": "test-ws", "environment": "dev", "aws_account_id": "123456789012", "region": "us-east-1"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


async def test_operator_cannot_delete_workspace(auth_client, operator_token, admin_token):
    """Operator cannot delete workspace (admin only)."""
    # First create a workspace as admin
    create_resp = await auth_client.post(
        "/api/v1/workspaces",
        json={"name": "del-ws", "environment": "dev", "aws_account_id": "123456789012", "region": "us-east-1"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ws_id = create_resp.json()["id"]

    response = await auth_client.delete(
        f"/api/v1/workspaces/{ws_id}",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert response.status_code == 403


async def test_admin_can_create_workspace(auth_client, admin_token):
    """Admin can create workspace -> 201."""
    response = await auth_client.post(
        "/api/v1/workspaces",
        json={"name": "new-ws", "environment": "dev", "aws_account_id": "123456789012", "region": "us-east-1"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "new-ws"


async def test_viewer_can_list_workspaces(auth_client, viewer_token):
    """Viewer can GET /workspaces (viewer+ role)."""
    response = await auth_client.get(
        "/api/v1/workspaces",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200


async def test_operator_can_update_workspace(auth_client, admin_token, operator_token):
    """Operator can PUT workspace (operator+ role)."""
    # Create workspace as admin first
    create_resp = await auth_client.post(
        "/api/v1/workspaces",
        json={"name": "upd-ws", "environment": "dev", "aws_account_id": "123456789012", "region": "us-east-1"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ws_id = create_resp.json()["id"]

    response = await auth_client.put(
        f"/api/v1/workspaces/{ws_id}",
        json={"name": "upd-ws-renamed"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 3. Runs tests
# ---------------------------------------------------------------------------

async def test_operator_can_trigger_plan(auth_client, admin_token, operator_token):
    """Operator can trigger a plan run -> 201."""
    # Create workspace as admin
    create_resp = await auth_client.post(
        "/api/v1/workspaces",
        json={"name": "run-ws", "environment": "dev", "aws_account_id": "123456789012", "region": "us-east-1"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ws_id = create_resp.json()["id"]

    response = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert response.status_code == 201
    assert response.json()["command"] == "plan"
    assert response.json()["status"] == "pending"


async def test_viewer_cannot_trigger_run(auth_client, admin_token, viewer_token):
    """Viewer cannot trigger runs (operator+ required)."""
    create_resp = await auth_client.post(
        "/api/v1/workspaces",
        json={"name": "norun-ws", "environment": "dev", "aws_account_id": "123456789012", "region": "us-east-1"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ws_id = create_resp.json()["id"]

    response = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


async def test_list_runs(auth_client, admin_token, operator_token, viewer_token):
    """Viewer can list runs."""
    # Create workspace + run
    create_resp = await auth_client.post(
        "/api/v1/workspaces",
        json={"name": "lr-ws", "environment": "dev", "aws_account_id": "123456789012", "region": "us-east-1"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ws_id = create_resp.json()["id"]
    await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )

    response = await auth_client.get(
        "/api/v1/runs",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1


# ---------------------------------------------------------------------------
# 4. Approval tests (4-eyes was removed; any operator+ can approve)
# ---------------------------------------------------------------------------

async def test_triggerer_can_approve_own_run_on_dev(auth_client, admin_token, operator_token):
    """Post 4-eyes removal: the triggerer can approve their own dev-branch run.

    Pre-change this was a 403 on `dev`. The rule was revoked — only the
    awaiting-approval FSM check still gates approve, which is a 409, not 403.
    """
    create_resp = await auth_client.post(
        "/api/v1/workspaces",
        json={
            "name": "no-4eyes-ws",
            "environment": "dev",
            "aws_account_id": "123456789012",
            "region": "us-east-1",
            "repo_ref": "dev",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ws_id = create_resp.json()["id"]
    run_resp = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    run_id = run_resp.json()["id"]

    response = await auth_client.post(
        f"/api/v1/runs/{run_id}/approve",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert response.status_code != 403, response.text


async def test_triggerer_can_approve_own_run_off_dev(auth_client, admin_token, operator_token):
    """Cross-branch sanity: self-approval works on main too (always did)."""
    create_resp = await auth_client.post(
        "/api/v1/workspaces",
        json={
            "name": "self-approve-ws",
            "environment": "staging",
            "aws_account_id": "123456789012",
            "region": "us-east-1",
            "repo_ref": "main",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ws_id = create_resp.json()["id"]
    run_resp = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    run_id = run_resp.json()["id"]

    response = await auth_client.post(
        f"/api/v1/runs/{run_id}/approve",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert response.status_code != 403


async def test_different_user_can_approve_run(auth_client, admin_token, operator_token):
    """Admin (different user) can approve a run triggered by operator."""
    # Create workspace + run as operator
    create_resp = await auth_client.post(
        "/api/v1/workspaces",
        json={"name": "approve-ws", "environment": "dev", "aws_account_id": "123456789012", "region": "us-east-1"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ws_id = create_resp.json()["id"]
    run_resp = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    run_id = run_resp.json()["id"]

    # Admin approves -> should succeed
    response = await auth_client.post(
        f"/api/v1/runs/{run_id}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # Run must be in awaiting_approval state for approval to work,
    # but since we just created a pending run, the API should either
    # transition it or return 409 (not in approvable state).
    # For now we test that 4-eyes is NOT the reason for rejection.
    assert response.status_code != 403


# ---------------------------------------------------------------------------
# 5. Users endpoint (admin only)
# ---------------------------------------------------------------------------

async def test_admin_can_list_users(auth_client, admin_token):
    """Admin can GET /users."""
    response = await auth_client.get(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1


async def test_viewer_cannot_list_users(auth_client, viewer_token):
    """Viewer cannot GET /users (admin only)."""
    response = await auth_client.get(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
