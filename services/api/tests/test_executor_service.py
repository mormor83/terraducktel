"""Tests for ExecutorService (CRITICAL-1) and credential scrubbing (CRITICAL-2).

TDD: RED phase — these tests must fail before implementation.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# CRITICAL-1: FSM transition guard on Run model
# ---------------------------------------------------------------------------

class TestRunStatusFSM:
    """Run.transition() guards valid/invalid status transitions."""

    async def test_run_status_fsm_valid_transitions(self, db_session):
        """Valid FSM transitions succeed without raising."""
        from app.models.run import Run, RunStatus

        run = Run(workspace_id="ws-1", command="plan")
        db_session.add(run)
        await db_session.commit()
        await db_session.refresh(run)

        # PENDING → RUNNING
        run.transition(RunStatus.RUNNING)
        assert run.status == RunStatus.RUNNING

        # RUNNING → PLANNING
        run.transition(RunStatus.PLANNING)
        assert run.status == RunStatus.PLANNING

        # PLANNING → PLANNED
        run.transition(RunStatus.PLANNED)
        assert run.status == RunStatus.PLANNED

        # PLANNED → AWAITING_APPROVAL
        run.transition(RunStatus.AWAITING_APPROVAL)
        assert run.status == RunStatus.AWAITING_APPROVAL

        # AWAITING_APPROVAL → APPLYING
        run.transition(RunStatus.APPLYING)
        assert run.status == RunStatus.APPLYING

        # APPLYING → APPLIED
        run.transition(RunStatus.APPLIED)
        assert run.status == RunStatus.APPLIED

    async def test_run_status_fsm_invalid_transition(self, db_session):
        """APPLIED → RUNNING must raise ValueError (terminal state)."""
        from app.models.run import Run, RunStatus

        run = Run(workspace_id="ws-1", command="apply")
        db_session.add(run)
        await db_session.commit()
        await db_session.refresh(run)

        # Fast-forward to APPLIED via valid transitions (must go through approval)
        run.transition(RunStatus.RUNNING)
        run.transition(RunStatus.PLANNING)
        run.transition(RunStatus.PLANNED)
        run.transition(RunStatus.AWAITING_APPROVAL)
        run.transition(RunStatus.APPLYING)
        run.transition(RunStatus.APPLIED)

        with pytest.raises(ValueError, match="Invalid transition"):
            run.transition(RunStatus.RUNNING)

    async def test_planning_to_failed_valid(self, db_session):
        """PLANNING → FAILED is a valid transition."""
        from app.models.run import Run, RunStatus

        run = Run(workspace_id="ws-1", command="plan")
        db_session.add(run)
        await db_session.commit()
        await db_session.refresh(run)

        run.transition(RunStatus.RUNNING)
        run.transition(RunStatus.PLANNING)
        run.transition(RunStatus.FAILED)
        assert run.status == RunStatus.FAILED

    async def test_terminal_failed_raises(self, db_session):
        """FAILED → anything raises ValueError."""
        from app.models.run import Run, RunStatus

        run = Run(workspace_id="ws-1", command="plan")
        db_session.add(run)
        await db_session.commit()
        await db_session.refresh(run)

        run.transition(RunStatus.RUNNING)
        run.transition(RunStatus.PLANNING)
        run.transition(RunStatus.FAILED)

        with pytest.raises(ValueError, match="Invalid transition"):
            run.transition(RunStatus.PENDING)


# ---------------------------------------------------------------------------
# CRITICAL-2: Credential scrubbing
# ---------------------------------------------------------------------------

class TestScrubCredentials:
    """scrub_credentials() redacts AWS keys from text."""

    def test_plan_output_scrubbed_of_aws_keys(self):
        """AWS access key ID pattern AKIA... is redacted."""
        from app.models.run import scrub_credentials

        text = "Configured with AKIAIOSFODNN7EXAMPLE as access key"
        result = scrub_credentials(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED-AWS-KEY]" in result

    def test_scrub_empty_string_passthrough(self):
        """Empty string passes through without error."""
        from app.models.run import scrub_credentials

        assert scrub_credentials("") == ""
        assert scrub_credentials(None) is None  # type: ignore[arg-type]

    def test_scrub_clean_text_unchanged(self):
        """Text without credentials is returned unmodified."""
        from app.models.run import scrub_credentials

        text = "Plan: 2 to add, 0 to change, 0 to destroy."
        assert scrub_credentials(text) == text


# ---------------------------------------------------------------------------
# CRITICAL-1: ExecutorService
# ---------------------------------------------------------------------------

class TestExecutorService:
    """ExecutorService.launch_run() spawns a Docker container."""

    async def test_launch_run_spawns_container(self):
        """launch_run calls docker containers.run with expected env vars."""
        from app.services.executor_service import ExecutorService
        from app.models.run import Run, RunStatus
        from app.models.workspace import Workspace

        mock_docker = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123deadbeef"
        mock_docker.containers.run.return_value = mock_container

        mock_config = MagicMock()
        mock_config.get = AsyncMock(side_effect=lambda k: {
            "aws.access_key_id": "AKIATEST00000000TEST",
            "aws.secret_access_key": "supersecretkey1234567890ABCDEFGHIJKLMNOP",
            "aws.region": "us-east-1",
        }.get(k))

        svc = ExecutorService(docker_client=mock_docker, config_service=mock_config)

        run = Run(id="run-1", workspace_id="ws-1", command="plan", status=RunStatus.PENDING)
        workspace = Workspace(
            id="ws-1",
            name="vpc",
            aws_account_id="123456789012",
            environment="dev",
            region="us-east-1",
            repo_url="https://example.com/repo.git",
        )

        await svc.launch_run(run, workspace)

        mock_docker.containers.run.assert_called_once()
        call_kwargs = mock_docker.containers.run.call_args[1]
        assert call_kwargs.get("detach") is True
        env = call_kwargs.get("environment", {})
        assert "AWS_ACCESS_KEY_ID" in env
        assert "AWS_SECRET_ACCESS_KEY" in env

    async def test_launch_run_drops_privileges_and_caps_resources(self):
        """Executor containers run workspace-supplied (semi-trusted) Terraform/
        Helm code — must never get more Linux capabilities or resources than
        the non-root `USER executor` in the image already has. Regression
        test for the hardening pass alongside ."""
        from app.services.executor_service import ExecutorService
        from app.models.run import Run, RunStatus
        from app.models.workspace import Workspace

        mock_docker = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123deadbeef"
        mock_docker.containers.run.return_value = mock_container

        mock_config = MagicMock()
        mock_config.get = AsyncMock(side_effect=lambda k: {
            "aws.access_key_id": "AKIATEST00000000TEST",
            "aws.secret_access_key": "supersecretkey1234567890ABCDEFGHIJKLMNOP",
            "aws.region": "us-east-1",
        }.get(k))

        svc = ExecutorService(docker_client=mock_docker, config_service=mock_config)

        run = Run(id="run-hardening", workspace_id="ws-1", command="plan", status=RunStatus.PENDING)
        workspace = Workspace(
            id="ws-1",
            name="vpc",
            aws_account_id="123456789012",
            environment="dev",
            region="us-east-1",
            repo_url="https://example.com/repo.git",
        )

        await svc.launch_run(run, workspace)

        call_kwargs = mock_docker.containers.run.call_args[1]
        assert call_kwargs.get("cap_drop") == ["ALL"]
        assert call_kwargs.get("security_opt") == ["no-new-privileges:true"]
        assert call_kwargs.get("mem_limit")
        assert isinstance(call_kwargs.get("pids_limit"), int) and call_kwargs["pids_limit"] > 0

    async def test_launch_run_sets_status_to_running(self):
        """launch_run transitions run status to RUNNING."""
        from app.services.executor_service import ExecutorService
        from app.models.run import Run, RunStatus
        from app.models.workspace import Workspace

        mock_docker = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123deadbeef"
        mock_docker.containers.run.return_value = mock_container

        mock_config = MagicMock()
        mock_config.get = AsyncMock(side_effect=lambda k: {
            "aws.access_key_id": "AKIATEST00000000TEST",
            "aws.secret_access_key": "supersecretkey1234567890ABCDEFGHIJKLMNOP",
            "aws.region": "us-east-1",
        }.get(k))

        svc = ExecutorService(docker_client=mock_docker, config_service=mock_config)

        run = Run(id="run-2", workspace_id="ws-1", command="plan", status=RunStatus.PENDING)
        workspace = Workspace(
            id="ws-1",
            name="vpc",
            aws_account_id="123456789012",
            environment="dev",
            region="us-east-1",
            repo_url="https://example.com/repo.git",
        )

        await svc.launch_run(run, workspace)

        assert run.status == RunStatus.RUNNING


# ---------------------------------------------------------------------------
# CRITICAL-3: S3StateService exception handling
# ---------------------------------------------------------------------------

class TestS3StateServiceExceptionHandling:
    """S3StateService re-raises non-NoSuchKey errors."""

    async def test_s3_get_state_connectivity_failure_raises(self):
        """Non-NoSuchKey ClientError propagates instead of returning None."""
        from botocore.exceptions import ClientError
        from app.services.s3_state_service import S3StateService

        svc = S3StateService(bucket="test-bucket")

        error_response = {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}
        exc = ClientError(error_response, "GetObject")

        with patch.object(svc, "_client") as mock_client:
            mock_client.get_object.side_effect = exc
            with pytest.raises(ClientError):
                svc.get_state("123456789012", "dev", "vpc")

    async def test_s3_get_state_no_such_key_returns_none(self):
        """NoSuchKey error returns None (object doesn't exist yet)."""
        from botocore.exceptions import ClientError
        from app.services.s3_state_service import S3StateService

        svc = S3StateService(bucket="test-bucket")

        error_response = {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}
        exc = ClientError(error_response, "GetObject")

        with patch.object(svc, "_client") as mock_client:
            mock_client.get_object.side_effect = exc
            result = svc.get_state("123456789012", "dev", "vpc")
            assert result is None
