"""Phase-2 executor contract tests.

Verifies:
1. RunUpdate schema accepts BOTH `plan_output` (canonical) and `output` (legacy)
   and coalesces to `plan_output`.
2. PATCH /api/v1/runs/{id} with {"status":"planned","plan_output":"hello"} writes
   `hello` into the DB column `plan_output`.
3. PATCH /api/v1/runs/{id} with the legacy {"output":"hello"} body shape also
   writes `hello` (back-compat path while old executor images still in flight).
4. ExecutorService.launch_run requires REPO_URL on the workspace and
   TERRADUCKTEL_STATE_TOKEN in the environment.
"""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID

import os
import uuid

import pytest


def test_run_update_accepts_canonical_plan_output():
    from app.schemas.run import RunUpdate

    body = RunUpdate.model_validate({"status": "planned", "plan_output": "hello"})
    assert body.plan_output == "hello"


def test_run_update_coalesces_legacy_output_field():
    from app.schemas.run import RunUpdate

    body = RunUpdate.model_validate({"status": "planned", "output": "hello"})
    assert body.plan_output == "hello"


def test_run_update_canonical_wins_when_both_present():
    from app.schemas.run import RunUpdate

    body = RunUpdate.model_validate(
        {"status": "planned", "plan_output": "canonical", "output": "legacy"}
    )
    assert body.plan_output == "canonical"


def test_run_create_rejects_arbitrary_command():
    from pydantic import ValidationError

    from app.schemas.run import RunCreate

    with pytest.raises(ValidationError):
        RunCreate.model_validate({"command": "rm -rf /"})


@pytest.mark.asyncio
async def test_patch_run_with_legacy_output_field_writes_plan_output(
    auth_client, seeded_users, operator_token, _setup_db
):
    """Round-trip: simulate an old executor that still sends `output`."""
    from app.models.workspace import Workspace
    from app.models.run import Run, RunStatus

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"contract-{ws_id[:8]}",
            repo_url="https://example.com/repo.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="plan",
            status=RunStatus.PLANNING,
        ))
        await session.commit()

    response = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "planned", "output": "legacy plan text"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )

    assert response.status_code == 200, response.text
    from sqlalchemy import select
    async with factory() as session:
        r = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
        assert r.plan_output == "legacy plan text"


@pytest.mark.asyncio
async def test_executor_service_requires_repo_url(monkeypatch):
    """ExecutorService.launch_run rejects workspaces without repo_url."""
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "test-state-token-do-not-use-in-prod")

    from app.services.executor_service import ExecutorService

    class FakeConfig:
        async def get(self, key):
            return ""

    class FakeRun:
        id = "run-1"
        command = "plan"

        def transition(self, _):
            pass

    class FakeWs:
        id = "ws-1"
        repo_url = None
        region = "us-east-1"
        tf_working_dir = "."

    svc = ExecutorService(docker_client=None, config_service=FakeConfig())
    with pytest.raises(RuntimeError, match="repo_url"):
        await svc.launch_run(FakeRun(), FakeWs(), api_token="dummy")


def test_executor_service_does_not_inject_global_state_token():
    """launch_run must NOT put the global TERRADUCKTEL_STATE_TOKEN into
    the executor env — the run-scoped API_TOKEN carries state auth instead."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(here, "app", "services", "executor_service.py")
    with open(src, "r") as f:
        text = f.read()
    assert '"TERRADUCKTEL_STATE_TOKEN": state_token' not in text
    assert '"API_TOKEN": api_token' in text


def test_entrypoint_uses_canonical_field_and_run_token():
    """Static check: executor entrypoint sends `plan_output` and authenticates to
    the state backend via HTTP Basic using the run-scoped API_TOKEN.

    Terraform 1.x's `backend "http"` does not support arbitrary headers; auth must
    flow through `TF_HTTP_USERNAME` / `TF_HTTP_PASSWORD` (HTTP Basic).
    """
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    entrypoint = os.path.join(here, "executor", "entrypoint.sh")
    with open(entrypoint, "r") as f:
        text = f.read()
    assert "plan_output" in text, "executor entrypoint must send plan_output (not output)"
    assert 'TF_HTTP_PASSWORD="${API_TOKEN}"' in text, (
        "executor entrypoint must export TF_HTTP_PASSWORD=API_TOKEN for the http backend"
    )
    assert "git clone" in text
    assert "backend \"http\"" in text
    # Defense in depth: ensure the legacy non-existent env var is not used.
    assert "TF_HTTP_HEADERS" not in text, (
        "TF_HTTP_HEADERS is not a real Terraform env var — auth must use Basic"
    )
