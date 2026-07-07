"""Security hardening: secret scan, validation, optional image/policy scans (Phase 5)."""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_TF = _REPO_ROOT / "terraform" / "example-workspace"
_POLICIES = _REPO_ROOT / "policies"


def test_trivy_scan_api_image_no_critical_cves():
    """API image has no CRITICAL CVEs when Trivy is available and image exists."""
    if shutil.which("trivy") is None:
        pytest.skip("trivy not installed")
    result = subprocess.run(
        [
            "trivy",
            "image",
            "--severity",
            "CRITICAL",
            "--exit-code",
            "1",
            "--no-progress",
            "terraducktel-api:test",
        ],
        capture_output=True,
        text=True,
    )
    combined = (result.stderr or "") + (result.stdout or "")
    if result.returncode != 0 and (
        "No such image" in combined or "unable to find the specified image" in combined
    ):
        pytest.skip("terraducktel-api:test image not built locally")
    assert result.returncode == 0, result.stdout + result.stderr


def test_trivy_scan_executor_image_no_critical_cves():
    if shutil.which("trivy") is None:
        pytest.skip("trivy not installed")
    result = subprocess.run(
        [
            "trivy",
            "image",
            "--severity",
            "CRITICAL",
            "--exit-code",
            "1",
            "--no-progress",
            "terraducktel-executor:test",
        ],
        capture_output=True,
        text=True,
    )
    combined = (result.stderr or "") + (result.stdout or "")
    if result.returncode != 0 and (
        "No such image" in combined or "unable to find the specified image" in combined
    ):
        pytest.skip("terraducktel-executor:test image not built locally")
    assert result.returncode == 0, result.stdout + result.stderr


def test_checkov_passes_on_example_workspace():
    if shutil.which("checkov") is None:
        pytest.skip("checkov not installed")
    checkov_yaml = _REPO_ROOT / ".checkov.yaml"
    cmd = [
        "checkov",
        "--directory",
        str(_EXAMPLE_TF),
        "--quiet",
        "--compact",
    ]
    if checkov_yaml.is_file():
        cmd.extend(["--config-file", str(checkov_yaml)])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    assert result.returncode == 0, result.stdout + result.stderr


async def test_state_with_secrets_rejected(auth_client: AsyncClient, admin_token: str):
    malicious_state = {
        "version": 4,
        "terraform_version": "1.7.0",
        "resources": [
            {
                "type": "aws_instance",
                "values": {"user_data": "aws_secret_key=AKIAIOSFODNN7EXAMPLE"},
            }
        ],
    }
    create = await auth_client.post(
        "/api/v1/workspaces",
        json={
            "name": "state-scan-ws",
            "environment": "dev",
            "aws_account_id": "123456789012",
            "region": "us-east-1",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 201
    ws_id = create.json()["id"]
    response = await auth_client.post(
        f"/api/v1/state/{ws_id}",
        json=malicious_state,
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-Terraducktel-State-Token": "test-state-token-do-not-use-in-prod",
        },
    )
    assert response.status_code == 422
    assert "secret" in response.json()["detail"].lower()


async def test_sql_injection_in_workspace_name_rejected(auth_client: AsyncClient, admin_token: str):
    response = await auth_client.post(
        "/api/v1/workspaces",
        json={
            "name": "'; DROP TABLE workspaces; --",
            "environment": "dev",
            "aws_account_id": "123456789012",
            "region": "us-east-1",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


async def test_jwt_secret_not_in_response(auth_client: AsyncClient, admin_token: str):
    jwt_secret = os.environ.get("JWT_SECRET_KEY", "test-secret-key-for-ci-not-production")
    response = await auth_client.get(
        "/api/v1/workspaces",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert jwt_secret not in response.text


def test_api_container_runs_as_non_root():
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "id", "terraducktel-api:test"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("terraducktel-api:test image not available")
    assert "uid=0(root)" not in result.stdout, result.stdout


def test_opa_deny_destructive_resource():
    if shutil.which("conftest") is None:
        pytest.skip("conftest not installed")
    if not _POLICIES.is_dir():
        pytest.skip("policies directory missing")
    import tempfile

    input_obj = {
        "resource_changes": [
            {"address": "aws_s3_bucket.data", "change": {"actions": ["delete"]}}
        ],
        "metadata": {"override_approved": False},
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(input_obj, f)
        tmp_path = f.name
    try:
        result = subprocess.run(
            [
                "conftest",
                "test",
                "--policy",
                str(_POLICIES),
                tmp_path,
            ],
            capture_output=True,
            text=True,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    assert result.returncode != 0, result.stdout + result.stderr
