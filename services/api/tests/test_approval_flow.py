"""Approval workflow: FSM, Slack hook, audit log, RBAC (Phase 4)."""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from unittest.mock import AsyncMock, patch

import pytest


async def _create_workspace(auth_client, admin_token: str) -> str:
    create_resp = await auth_client.post(
        "/api/v1/workspaces",
        json={
            "name": "approval-ws",
            "environment": "dev",
            "aws_account_id": "123456789012",
            "region": "us-east-1",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_resp.status_code == 201
    return create_resp.json()["id"]


async def _advance_run_to_awaiting_approval(auth_client, operator_token: str, run_id: str) -> None:
    """Simulate executor progressing the run until it awaits human approval."""
    chain = [
        ("running", None),
        ("planning", None),
        ("planned", None),
        ("awaiting_approval", "Plan: will create 1 resource"),
    ]
    for status, plan_out in chain:
        payload: dict = {"status": status}
        if plan_out is not None:
            payload["plan_output"] = plan_out
        r = await auth_client.patch(
            f"/api/v1/runs/{run_id}",
            json=payload,
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert r.status_code == 200, r.text


async def test_plan_run_enters_awaiting_approval_state(
    auth_client, admin_token, operator_token
):
    """After simulated plan completes, run is in awaiting_approval."""
    ws_id = await _create_workspace(auth_client, admin_token)
    run_resp = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]
    await _advance_run_to_awaiting_approval(auth_client, operator_token, run_id)
    status_resp = await auth_client.get(
        f"/api/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert status_resp.json()["status"] == "awaiting_approval"


async def test_approve_transitions_to_applying(
    auth_client, admin_token, operator_token
):
    """Approve moves run to applying."""
    ws_id = await _create_workspace(auth_client, admin_token)
    run_resp = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    run_id = run_resp.json()["id"]
    await _advance_run_to_awaiting_approval(auth_client, operator_token, run_id)

    response = await auth_client.post(
        f"/api/v1/runs/{run_id}/approve",
        json={"comment": "LGTM, approved for apply"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["new_status"] == "applying"


async def test_viewer_cannot_approve(auth_client, admin_token, operator_token, viewer_token):
    """Viewer cannot approve runs."""
    ws_id = await _create_workspace(auth_client, admin_token)
    run_resp = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    run_id = run_resp.json()["id"]
    await _advance_run_to_awaiting_approval(auth_client, operator_token, run_id)

    response = await auth_client.post(
        f"/api/v1/runs/{run_id}/approve",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


async def test_slack_notification_sent_on_plan_completion(
    auth_client, admin_token, operator_token
):
    """Slack sender is invoked when run enters awaiting_approval."""
    with patch(
        "app.routers.runs.send_plan_approval_notification",
        new_callable=AsyncMock,
    ) as mock_notify:
        ws_id = await _create_workspace(auth_client, admin_token)
        run_resp = await auth_client.post(
            f"/api/v1/workspaces/{ws_id}/runs",
            json={"command": "plan"},
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        run_id = run_resp.json()["id"]
        await _advance_run_to_awaiting_approval(auth_client, operator_token, run_id)
        mock_notify.assert_called()


async def test_audit_log_records_approval(auth_client, admin_token, operator_token):
    """Approve writes an audit row with action approve."""
    ws_id = await _create_workspace(auth_client, admin_token)
    run_resp = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    run_id = run_resp.json()["id"]
    await _advance_run_to_awaiting_approval(auth_client, operator_token, run_id)

    await auth_client.post(
        f"/api/v1/runs/{run_id}/approve",
        json={"comment": "approved"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    audit_resp = await auth_client.get(
        f"/api/v1/audit?run_id={run_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert audit_resp.status_code == 200
    entries = audit_resp.json()["items"]
    assert any(e["action"] == "approve" for e in entries)


async def test_reject_is_terminal(auth_client, admin_token, operator_token):
    """Reject ends in cancelled terminal state."""
    ws_id = await _create_workspace(auth_client, admin_token)
    run_resp = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    run_id = run_resp.json()["id"]
    await _advance_run_to_awaiting_approval(auth_client, operator_token, run_id)

    response = await auth_client.post(
        f"/api/v1/runs/{run_id}/reject",
        json={"comment": "Not safe to apply in production now"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["new_status"] == "cancelled"

    again = await auth_client.post(
        f"/api/v1/runs/{run_id}/approve",
        json={"comment": "retry"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert again.status_code == 409
