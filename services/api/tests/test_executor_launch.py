"""Coverage for ExecutorService.launch_run (docker + ECS runtimes), credential
resolution, helm kubeconfig, azure ARM injection, variable merge, local-repo
premount, modules mirror, and the ECS RunTask path. No real docker/boto3."""
import json
import uuid

import pytest

from app.services.executor_service import ExecutorService
from app.services.config_service import ConfigService
from app.services import aws_account_service as accs
from app.services import azure_subscription_service as azsvc
from app.services import cluster_service as clustersvc
from app.services import variable_service as varsvc
from app.schemas.variable import VariableCreate
from app.auth.encryption_key import get_credential_encryption_key
from app.models.aws_account import AwsAccount
from app.models.azure_subscription import AzureSubscription
from app.models.business_unit import DEFAULT_BU_ID, BusinessUnit
from app.models.run import Run, RunStatus
from app.models.workspace import Workspace


# ─── fakes ───────────────────────────────────────────────────────────────────


class _Container:
    def __init__(self):
        self.id = "c" * 20


class _Containers:
    def __init__(self):
        self.kwargs = None

    def run(self, **kw):
        self.kwargs = kw
        return _Container()


class _Docker:
    def __init__(self):
        self.containers = _Containers()


def _svc(db_session, docker=None):
    return ExecutorService(docker, ConfigService(db_session, get_credential_encryption_key()))


async def _seed(db_session, **ws_over):
    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
    db_session.add(
        AwsAccount(
            business_unit_id=DEFAULT_BU_ID, account_id="123456789012", name="a",
            state_bucket="b", default_region="eu-west-1",
            access_key_id_encrypted=accs.encrypt_secret("AKIA"),
            secret_access_key_encrypted=accs.encrypt_secret("sek"),
        )
    )
    ws_kwargs = dict(
        business_unit_id=DEFAULT_BU_ID, name="ws", aws_account_id="123456789012",
        region="us-east-1", environment="dev", repo_url="https://github.com/o/r.git",
        tf_working_dir="account-1/us-east-1/leaf", repo_ref="main",
    )
    ws_kwargs.update(ws_over)
    ws = Workspace(**ws_kwargs)
    db_session.add(ws)
    await db_session.commit()
    run = Run(id=str(uuid.uuid4()), workspace_id=ws.id, command="plan", status=RunStatus.PENDING)
    db_session.add(run)
    await db_session.commit()
    return ws, run


# ─── guard rails ─────────────────────────────────────────────────────────────


async def test_launch_requires_repo_url(db_session):
    ws, run = await _seed(db_session, repo_url="")
    with pytest.raises(RuntimeError, match="no repo_url"):
        await _svc(db_session, _Docker()).launch_run(run, ws, db_session=db_session)


async def test_launch_no_longer_requires_global_state_token(db_session, monkeypatch):
    """the executor authenticates to the state backend with its
    run-scoped API_TOKEN, so a missing global TERRADUCKTEL_STATE_TOKEN no longer
    blocks launch, and the token is NOT injected into the container."""
    monkeypatch.delenv("TERRADUCKTEL_STATE_TOKEN", raising=False)
    ws, run = await _seed(db_session)
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, api_token="tok", db_session=db_session)
    env = docker.containers.kwargs["environment"]
    assert "TERRADUCKTEL_STATE_TOKEN" not in env
    assert env["API_TOKEN"] == "tok"


async def test_docker_runtime_requires_client(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    ws, run = await _seed(db_session)
    with pytest.raises(RuntimeError, match="without a Docker client"):
        await _svc(db_session, None).launch_run(run, ws, db_session=db_session)


# ─── terraform docker happy path ─────────────────────────────────────────────


async def test_terraform_docker_launch_full_env(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "test-state-token-do-not-use-in-prod")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    ws, run = await _seed(db_session, azure_subscription_id=None)
    await varsvc.create_global(db_session, VariableCreate(key="LOG", value="DEBUG"), DEFAULT_BU_ID)
    await db_session.commit()
    docker = _Docker()
    container = await _svc(db_session, docker).launch_run(run, ws, api_token="tok", db_session=db_session)
    assert container.id.startswith("cc")
    env = docker.containers.kwargs["environment"]
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA" and env["AWS_SECRET_ACCESS_KEY"] == "sek"
    assert env["AWS_DEFAULT_REGION"] == "eu-west-1"
    assert env["TF_VAR_LOG"] == "DEBUG"
    assert env["WORKSPACE_KIND"] == "terraform" and env["TF_PHASE"] == "plan"
    # run-scoped API_TOKEN carries state auth; global state token gone.
    assert env["API_TOKEN"] == "tok"
    assert "TERRADUCKTEL_STATE_TOKEN" not in env
    assert run.status == RunStatus.RUNNING


async def test_global_cred_fallback_when_no_account(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    ws, run = await _seed(db_session, aws_account_id="000000000099")
    cfg = ConfigService(db_session, get_credential_encryption_key())
    await cfg.set("aws.access_key_id", "GLOBALKEY")
    await cfg.set("aws.secret_access_key", "GLOBALSEC")
    await cfg.set("aws.region", "ap-south-1")
    await db_session.commit()
    docker = _Docker()
    await ExecutorService(docker, cfg).launch_run(run, ws, db_session=db_session)
    env = docker.containers.kwargs["environment"]
    assert env["AWS_ACCESS_KEY_ID"] == "GLOBALKEY" and env["AWS_DEFAULT_REGION"] == "ap-south-1"


async def test_apply_phase_does_not_retransition(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    ws, run = await _seed(db_session)
    run.status = RunStatus.APPLYING
    await db_session.commit()
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session, phase="apply")
    assert run.status == RunStatus.APPLYING
    assert docker.containers.kwargs["environment"]["TF_PHASE"] == "apply"


# ─── azure injection ─────────────────────────────────────────────────────────


async def test_azure_arm_env_injected(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    sub = AzureSubscription(
        business_unit_id=DEFAULT_BU_ID, subscription_id="s", tenant_id="t1", client_id="c1",
        client_secret_encrypted=azsvc.encrypt_secret("sp"), name="az", default_location="eastus",
    )
    db_session.add(sub)
    await db_session.commit()
    ws, run = await _seed(db_session, azure_subscription_id=sub.id)
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session)
    env = docker.containers.kwargs["environment"]
    assert env["ARM_CLIENT_ID"] == "c1" and env["ARM_CLIENT_SECRET"] == "sp"
    assert env["ARM_SUBSCRIPTION_ID"] == "s" and env["TDT_CLOUD_PROVIDERS"] == "aws,azure"


# ─── helm path ───────────────────────────────────────────────────────────────


async def test_helm_requires_cluster_id(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    ws, run = await _seed(db_session, kind="helm", cluster_id=None)
    with pytest.raises(RuntimeError, match="no cluster_id"):
        await _svc(db_session, _Docker()).launch_run(run, ws, db_session=db_session)


async def test_helm_launch_injects_kubeconfig(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
        await db_session.commit()
    cluster = await clustersvc.create_cluster(
        db_session, business_unit_id=DEFAULT_BU_ID, name="c", kubeconfig="KCFG",
        default_namespace="ops",
    )
    ws, run = await _seed(db_session, kind="helm", cluster_id=cluster.id, aws_account_id="global")
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session)
    env = docker.containers.kwargs["environment"]
    assert env["KUBECONFIG_CONTENT"] == "KCFG" and env["WORKSPACE_KIND"] == "helm"
    assert env["KUBE_DEFAULT_NAMESPACE"] == "ops"


# ─── local-repo premount + modules mirror ────────────────────────────────────


async def test_local_repo_premount_and_modules_volume(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    monkeypatch.setenv("TERRADUCKTEL_LOCAL_REPOS_HOST_DIR", "/host/repos")
    monkeypatch.setenv("TERRADUCKTEL_LOCAL_REPOS_DIR", "/mnt/local-repos")
    ws, run = await _seed(db_session, repo_url="local:///mnt/local-repos/myrepo")
    cfg = ConfigService(db_session, get_credential_encryption_key())
    await cfg.set(
        "modules.config",
        json.dumps({"mode": "local", "upstream_url": "https://reg", "local_host_dir": "/host/mods"}),
    )
    await db_session.commit()
    docker = _Docker()
    await ExecutorService(docker, cfg).launch_run(run, ws, db_session=db_session)
    vols = docker.containers.kwargs["volumes"]
    assert "/host/repos/myrepo" in vols
    assert any(b["bind"] == "/terraducktel/modules-mirror" for b in vols.values())
    assert docker.containers.kwargs["environment"]["REPO_PREMOUNTED"] == "1"


# ─── ECS runtime ─────────────────────────────────────────────────────────────


async def test_ecs_runtime_missing_targeting_env(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "ecs")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    for k in ("EXECUTOR_CLUSTER", "EXECUTOR_TASK_DEF", "EXECUTOR_SUBNETS", "EXECUTOR_SG"):
        monkeypatch.delenv(k, raising=False)
    ws, run = await _seed(db_session)
    with pytest.raises(RuntimeError, match="EXECUTOR_CLUSTER"):
        await _svc(db_session, None).launch_run(run, ws, db_session=db_session)


def _setup_ecs_env(monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "ecs")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    monkeypatch.setenv("EXECUTOR_CLUSTER", "arn:cluster")
    monkeypatch.setenv("EXECUTOR_TASK_DEF", "td")
    monkeypatch.setenv("EXECUTOR_SUBNETS", "subnet-1,subnet-2")
    monkeypatch.setenv("EXECUTOR_SG", "sg-1")


class _FakeEcs:
    def __init__(self, resp):
        self._resp = resp
        self.call = None

    def run_task(self, **kw):
        self.call = kw
        return self._resp


async def test_ecs_runtime_success(db_session, monkeypatch):
    _setup_ecs_env(monkeypatch)
    ws, run = await _seed(db_session)
    fake = _FakeEcs({"tasks": [{"taskArn": "arn/aws/task/abc"}], "failures": []})
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: fake)
    task = await _svc(db_session, None).launch_run(run, ws, db_session=db_session)
    assert task.id == "arn/aws/task/abc"
    assert any(
        e["name"] == "RUN_ID" for e in fake.call["overrides"]["containerOverrides"][0]["environment"]
    )


async def test_ecs_runtime_failures_and_no_tasks(db_session, monkeypatch):
    _setup_ecs_env(monkeypatch)
    ws, run = await _seed(db_session)
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _FakeEcs({"failures": [{"reason": "x"}]}))
    with pytest.raises(RuntimeError, match="RunTask failed"):
        await _svc(db_session, None).launch_run(run, ws, db_session=db_session)

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _FakeEcs({"tasks": []}))
    with pytest.raises(RuntimeError, match="no tasks"):
        await _svc(db_session, None).launch_run(run, ws, db_session=db_session)


# ─── remaining branches ──────────────────────────────────────────────────────


async def test_helm_empty_kubeconfig_raises(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
        await db_session.commit()
    cluster = await clustersvc.create_cluster(
        db_session, business_unit_id=DEFAULT_BU_ID, name="empty", kubeconfig="",
    )
    ws, run = await _seed(db_session, kind="helm", cluster_id=cluster.id, aws_account_id="global")
    with pytest.raises(RuntimeError, match="Could not resolve kubeconfig"):
        await _svc(db_session, _Docker()).launch_run(run, ws, db_session=db_session)


async def test_helm_cluster_eks_credentials_exported(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
        await db_session.commit()
    db_session.add(
        AwsAccount(
            business_unit_id=DEFAULT_BU_ID, account_id="123456789012", name="eks-acct",
            state_bucket="b", default_region="us-west-2",
            access_key_id_encrypted=accs.encrypt_secret("EKSKEY"),
            secret_access_key_encrypted=accs.encrypt_secret("EKSSEC"),
        )
    )
    await db_session.commit()
    cluster = await clustersvc.create_cluster(
        db_session, business_unit_id=DEFAULT_BU_ID, name="eks", kubeconfig="KC",
        aws_account_id="123456789012",
    )
    ws, run = await _seed(db_session, kind="helm", cluster_id=cluster.id, aws_account_id="global")
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session)
    env = docker.containers.kwargs["environment"]
    assert env["AWS_ACCESS_KEY_ID"] == "EKSKEY" and env["AWS_DEFAULT_REGION"] == "us-west-2"


async def test_helm_cluster_auth_load_failure_swallowed(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
        await db_session.commit()
    cluster = await clustersvc.create_cluster(
        db_session, business_unit_id=DEFAULT_BU_ID, name="eksx", kubeconfig="KC",
        aws_account_id="123456789012",
    )
    ws, run = await _seed(db_session, kind="helm", cluster_id=cluster.id, aws_account_id="global")
    # Make the in-block get_cluster raise → the except branch logs + continues.
    monkeypatch.setattr(clustersvc, "get_cluster", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session)
    assert docker.containers.kwargs["environment"]["WORKSPACE_KIND"] == "helm"


async def test_azure_cred_load_failure_swallowed(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    # Subscription with corrupt ciphertext → get_subscription_credentials raises
    # → except sets creds=None → no ARM_* env injected.
    sub = AzureSubscription(
        business_unit_id=DEFAULT_BU_ID, subscription_id="s", tenant_id="t1", client_id="c1",
        client_secret_encrypted="not-a-valid-fernet-token", name="az", default_location="eastus",
    )
    db_session.add(sub)
    await db_session.commit()
    ws, run = await _seed(db_session, azure_subscription_id=sub.id)
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session)
    assert "ARM_CLIENT_ID" not in docker.containers.kwargs["environment"]


async def test_modules_config_parse_failure_swallowed(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    ws, run = await _seed(db_session)
    cfg = ConfigService(db_session, get_credential_encryption_key())
    await cfg.set("modules.config", "{not valid json")
    await db_session.commit()
    docker = _Docker()
    await ExecutorService(docker, cfg).launch_run(run, ws, db_session=db_session)
    assert docker.containers.kwargs["environment"]["MODULES_UPSTREAM_URL"] == ""


async def test_variable_merge_failure_swallowed(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    ws, run = await _seed(db_session)
    monkeypatch.setattr(
        varsvc, "get_merged_for_run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("merge boom")),
    )
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session)
    # launch still succeeds; no TF_VAR_* injected
    assert not any(k.startswith("TF_VAR_") for k in docker.containers.kwargs["environment"])


async def test_unresolvable_bu_uses_global_config_path(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    ws, run = await _seed(db_session)
    # Point the workspace at a BU that doesn't exist → bu_slug stays None, so
    # _bu_or_global() falls through to the unscoped config.get() path.
    ws.business_unit_id = "ghost-bu"
    await db_session.commit()
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session)
    assert docker.containers.kwargs["environment"]["CHECKOV_MODE"] == "fail"
