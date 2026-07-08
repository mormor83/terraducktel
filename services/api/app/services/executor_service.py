"""ExecutorService — spawns ephemeral Docker containers for Terraform runs.

Uses Python docker SDK to launch the executor image with per-run credentials
fetched from the PG config table. Run status is updated via the FSM transition
method on the Run model.
"""
import logging
import os
from typing import Any

from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)


class ExecutorService:
    """Manages lifecycle of executor containers for Terraform runs."""

    EXECUTOR_IMAGE = os.environ.get("EXECUTOR_IMAGE", "terraducktel-executor:latest")
    # Helm workspaces (workspace.kind == "helm") launch a separate image that
    # bundles helm + kubectl + helm-diff + kubeconform. Same entrypoint, which
    # branches on WORKSPACE_KIND.
    EXECUTOR_HELM_IMAGE = os.environ.get(
        "EXECUTOR_HELM_IMAGE", "terraducktel-executor-helm:latest"
    )

    def __init__(self, docker_client: Any | None, config_service: ConfigService) -> None:
        # docker_client may be None when EXECUTOR_RUNTIME=ecs (Fargate has no
        # local Docker daemon). The ECS branch in launch_run uses boto3, not
        # self._docker; the docker branch raises if invoked with None.
        self._docker = docker_client
        self._config = config_service

    async def launch_run(
        self,
        run: Any,
        workspace: Any,
        api_token: str = "",
        db_session: Any = None,
        phase: str = "plan",
    ) -> Any:
        """Fetch credentials, build ephemeral env dict, spawn container.

        Transitions run.status to RUNNING before spawning.
        Returns the container object.

        Phase-8: AWS credentials are sourced from the per-account `aws_accounts`
        row (encrypted at rest, decrypted only here). Falls back to the global
        Config table for back-compat.
        """
        from app.models.run import RunStatus
        from app.services import aws_account_service as accs

        api_url = os.environ.get("PUBLIC_API_URL", "http://api:8000")

        repo_url = getattr(workspace, "repo_url", None) or ""
        if not repo_url:
            raise RuntimeError(
                f"Workspace {workspace.id} has no repo_url; cannot launch executor"
            )

        # Helm has NO external state backend (release state lives in-cluster),
        # so the state token + S3/HTTP-backend wiring is terraform-only.
        workspace_kind = (getattr(workspace, "kind", None) or "terraform").strip().lower()
        is_helm = workspace_kind == "helm"

        # the executor no longer receives the global TERRADUCKTEL_STATE_TOKEN
        # (which would let a workspace's Terraform read/overwrite ANY tenant's
        # state). It authenticates to the state backend with its run-scoped token
        # (api_token, workspace-bound) via TF_HTTP_PASSWORD instead.

        # Resolve the workspace's owning BU once; per-BU config keys use the
        # slug, and AWS credentials lookups must scope by BU since two BUs may
        # legally register the same 12-digit AWS account.
        bu_slug: str | None = None
        bu_id = getattr(workspace, "business_unit_id", None)
        if bu_id and db_session is not None:
            from app.models.business_unit import BusinessUnit

            bu_row = await db_session.get(BusinessUnit, bu_id)
            if bu_row is not None:
                bu_slug = bu_row.slug

        # Per-account creds (preferred); fall back to global Config table.
        #
        # Lookup precedence:
        #   1. workspace.state_aws_account_id — an explicit override for
        #      "use THIS account's creds for the state backend, regardless
        #      of which account owns the managed resources". Used for non-AWS
        #      workspaces (aws_account_id="global") whose terraform state
        #      lives in an AWS S3 bucket — keeps the workspace grouped under
        #      the non-AWS section of the dashboard while still letting the
        #      executor open the bucket with same-account creds.
        #   2. workspace.aws_account_id — the resource-owning account; what
        #      every existing AWS workspace continues to use.
        aws_key = ""
        aws_secret = ""
        aws_profile = ""
        aws_default_region_override = ""
        # Helm workspaces have no AWS account / state backend — skip the entire
        # AWS-cred resolution block. Connectivity comes from the cluster
        # kubeconfig instead (resolved below).
        if not is_helm:
            cred_account_id = (
                getattr(workspace, "state_aws_account_id", None)
                or workspace.aws_account_id
            )
            if db_session is not None:
                creds = await accs.list_account_credentials(
                    db_session, cred_account_id, business_unit_id=bu_id,
                )
                if creds is not None:
                    aws_key, aws_secret = creds
                # Pull the optional profile name + default region from the same row.
                account_row = await accs.get_account_by_account_id(
                    db_session, cred_account_id, business_unit_id=bu_id,
                )
                if account_row is not None:
                    aws_profile = (account_row.aws_profile_name or "").strip()
                    aws_default_region_override = (account_row.default_region or "").strip()
            if not aws_key:
                aws_key = await self._config.get("aws.access_key_id") or ""
            if not aws_secret:
                aws_secret = await self._config.get("aws.secret_access_key") or ""

        # Helm: resolve the decrypted kubeconfig for the workspace's linked
        # cluster. Mirrors the AWS-cred resolution pattern above. NEVER logged.
        kubeconfig_content = ""
        if is_helm and db_session is not None:
            cluster_id = getattr(workspace, "cluster_id", None)
            if not cluster_id:
                raise RuntimeError(
                    f"Helm workspace {workspace.id} has no cluster_id; link it to "
                    "a Kubernetes cluster before running."
                )
            from app.services import cluster_service as clustersvc

            kubeconfig_content = await clustersvc.get_cluster_kubeconfig(
                db_session, cluster_id
            ) or ""
            if not kubeconfig_content:
                raise RuntimeError(
                    f"Could not resolve kubeconfig for cluster {cluster_id} "
                    f"(helm workspace {workspace.id})."
                )

        # Optional GitHub PAT for downloading private terraform modules
        # (`module "x" { source = "git::https://github.com/..." }`). Read order:
        # env var (operator override) → BU-scoped config → legacy global.
        async def _bu_or_global(key: str) -> str | None:
            if bu_slug is not None:
                return await self._config.get_for_bu(bu_slug, key)
            return await self._config.get(key)

        github_token = (
            os.environ.get("GITHUB_TOKEN", "").strip()
            or (await _bu_or_global("github.token") or "").strip()
        )

        # Optional modules-registry redirect (Settings → Terraform modules).
        # When mode=local we bind-mount the host path into the executor and
        # the entrypoint rewrites the upstream URL to file://. JSON in DB.
        modules_upstream_url = ""
        modules_local_container_dir = ""
        modules_local_volume: tuple[str, str] | None = None
        try:
            import json as _json
            modules_raw = await _bu_or_global("modules.config") or ""
            if modules_raw:
                modules_cfg = _json.loads(modules_raw)
                if modules_cfg.get("mode") == "local":
                    upstream = (modules_cfg.get("upstream_url") or "").strip()
                    host_dir = (modules_cfg.get("local_host_dir") or "").strip()
                    if upstream and host_dir:
                        modules_upstream_url = upstream
                        modules_local_container_dir = "/terraducktel/modules-mirror"
                        modules_local_volume = (host_dir, modules_local_container_dir)
        except Exception:
            logger.warning("Failed to parse modules.config; skipping override", exc_info=True)

        environment = {
            "RUN_ID": run.id,
            "WORKSPACE_ID": workspace.id,
            "REPO_URL": repo_url,
            # Prefer the run's snapshotted branch (set at trigger time) so an
            # apply-phase executor uses the same ref as the plan-phase did,
            # even if the workspace's current branch was changed in between.
            "REPO_REF": getattr(run, "branch", None) or workspace.repo_ref or "main",
            "TF_WORKING_DIR": getattr(workspace, "tf_working_dir", "."),
            "TF_COMMAND": run.command,
            # Drives the entrypoint pipeline: "terraform" (default) or "helm".
            "WORKSPACE_KIND": workspace_kind,
            # `plan` (default): run plan steps, save tfplan, pause for approval
            # if the run was triggered as `apply`. `apply`: restore tfplan and
            # run `terraform apply` (post-approval).
            "TF_PHASE": phase,
            "API_URL": api_url,
            "API_TOKEN": api_token,
            "GITHUB_TOKEN": github_token,
            "MODULES_UPSTREAM_URL": modules_upstream_url,
            "MODULES_LOCAL_DIR": modules_local_container_dir,
            # `fail` (default) aborts the run on any Checkov finding; `warn`
            # captures violations as the step output but lets the run continue.
            # Per-BU via bu.<slug>.checkov.mode, with fallback to the legacy
            # global key during the transition.
            "CHECKOV_MODE": (await _bu_or_global("checkov.mode") or "fail").strip(),
            # OPA/conftest policy gate. `off` (default) skips the step entirely.
            # The enabled DB policies are NOT passed via env — the executor pulls
            # them from GET /runs/{id}/policies at run time. Per-BU via
            # bu.<slug>.opa.*; bundled/git sources take a source-level severity.
            "OPA_MODE": (await _bu_or_global("opa.mode") or "off").strip(),
            "OPA_USE_BUNDLED": (await _bu_or_global("opa.use_bundled") or "true").strip(),
            "OPA_BUNDLED_SEVERITY": (await _bu_or_global("opa.bundled_severity") or "block").strip(),
            "OPA_GIT_SEVERITY": (await _bu_or_global("opa.git_severity") or "block").strip(),
            "OPA_REPO_URL": (await _bu_or_global("opa.repo_url") or "").strip(),
            "OPA_REPO_REF": (await _bu_or_global("opa.repo_ref") or "main").strip(),
            "OPA_REPO_DIR": (await _bu_or_global("opa.repo_dir") or "").strip(),
            # Infracost is best-effort — empty key means the Cost Estimation
            # step gracefully `skipped`; non-empty enables breakdown JSON.
            "INFRACOST_API_KEY": (
                os.environ.get("INFRACOST_API_KEY", "").strip()
                or (await _bu_or_global("infracost.api_key") or "").strip()
            ),
            "INFRACOST_CURRENCY": (await _bu_or_global("infracost.currency") or "USD").strip(),
            "AWS_ACCESS_KEY_ID": aws_key,
            "AWS_SECRET_ACCESS_KEY": aws_secret,
            # Profile name fed into the executor so it can write
            # ~/.aws/credentials with the matching [profile] section.
            "AWS_PROFILE_NAME": aws_profile,
            "AWS_DEFAULT_REGION": (
                aws_default_region_override
                or (await self._config.get("aws.region"))
                or workspace.region
            ),
        }

        # Helm: inject the decrypted kubeconfig + optional default namespace so
        # the entrypoint can write ~/.kube/config and target the cluster. The
        # terraform HTTP-backend env vars stay empty (skipped for helm).
        if is_helm:
            environment["KUBECONFIG_CONTENT"] = kubeconfig_content
            kube_default_ns = ""
            if db_session is not None and getattr(workspace, "cluster_id", None):
                try:
                    from app.services import cluster_service as clustersvc

                    cluster_row = await clustersvc.get_cluster(
                        db_session, workspace.cluster_id
                    )
                    if cluster_row is not None:
                        kube_default_ns = (
                            getattr(cluster_row, "default_namespace", "") or ""
                        )
                        # EKS kubeconfigs auth via `aws eks get-token` (exec
                        # plugin) inside the executor — export the linked AWS
                        # account's creds so the plugin can mint a token.
                        eks_acct = getattr(cluster_row, "aws_account_id", None)
                        if eks_acct:
                            eks_creds = await accs.list_account_credentials(
                                db_session, eks_acct, business_unit_id=bu_id,
                            )
                            if eks_creds is not None:
                                environment["AWS_ACCESS_KEY_ID"] = eks_creds[0]
                                environment["AWS_SECRET_ACCESS_KEY"] = eks_creds[1]
                                eks_row = await accs.get_account_by_account_id(
                                    db_session, eks_acct, business_unit_id=bu_id,
                                )
                                if eks_row is not None and (eks_row.default_region or "").strip():
                                    environment["AWS_DEFAULT_REGION"] = eks_row.default_region.strip()
                except Exception:
                    logger.warning(
                        "Failed to load cluster auth for workspace %s",
                        workspace.id, exc_info=True,
                    )
            environment["KUBE_DEFAULT_NAMESPACE"] = kube_default_ns

        # Azure: if the workspace is linked to an Azure subscription, export the
        # standard ARM_* env vars so the terraform azurerm provider authenticates
        # via the configured service principal. AWS creds above stay populated
        # because the state backend continues to use S3 for this release.
        azure_sub_pk = getattr(workspace, "azure_subscription_id", None)
        if azure_sub_pk and db_session is not None:
            try:
                from app.services import azure_subscription_service as azsvc

                creds = await azsvc.get_subscription_credentials(db_session, azure_sub_pk)
            except Exception:
                logger.warning(
                    "Failed to load Azure SP creds for subscription %s — workspace %s will "
                    "fall back to environment auth (likely fails).",
                    azure_sub_pk, workspace.id, exc_info=True,
                )
                creds = None
            if creds is not None:
                sub_id, tenant_id, client_id, client_secret = creds
                environment.update({
                    "ARM_SUBSCRIPTION_ID": sub_id,
                    "ARM_TENANT_ID": tenant_id,
                    "ARM_CLIENT_ID": client_id,
                    "ARM_CLIENT_SECRET": client_secret,
                })

        # GCP: if the workspace is linked to a GCP project, pass the SA-key JSON
        # (the entrypoint writes it to a 0600 file and exports
        # GOOGLE_APPLICATION_CREDENTIALS) plus GOOGLE_PROJECT / GOOGLE_REGION so
        # the terraform google provider authenticates.
        gcp_project_pk = getattr(workspace, "gcp_project_id", None)
        if gcp_project_pk and db_session is not None:
            try:
                from app.services import gcp_project_service as gcpsvc

                gcp_creds = await gcpsvc.get_project_credentials(db_session, gcp_project_pk)
                gcp_row = await gcpsvc.get_project(db_session, gcp_project_pk)
            except Exception:
                logger.warning(
                    "Failed to load GCP SA creds for project %s — workspace %s will "
                    "fall back to environment auth (likely fails).",
                    gcp_project_pk, workspace.id, exc_info=True,
                )
                gcp_creds = None
                gcp_row = None
            if gcp_creds is not None:
                project_id, sa_json = gcp_creds
                gcp_region = (
                    (getattr(gcp_row, "default_region", None) or "").strip()
                    or workspace.region
                )
                environment.update({
                    "GCP_SA_KEY_JSON": sa_json,
                    "GOOGLE_PROJECT": project_id,
                    "GOOGLE_REGION": gcp_region,
                })

        # Tell the executor entrypoint which provider mix to expect — composed
        # from whatever creds actually got wired in above. Unset only when the
        # workspace has no cloud creds at all (entrypoint then defaults to AWS).
        _providers = []
        if aws_key:
            _providers.append("aws")
        if environment.get("ARM_CLIENT_ID"):
            _providers.append("azure")
        if environment.get("GCP_SA_KEY_JSON"):
            _providers.append("gcp")
        if _providers:
            environment["TDT_CLOUD_PROVIDERS"] = ",".join(_providers)

        # Merge global → workspace → run variables and inject as TF_VAR_<key>.
        # Terraform parses TF_VAR_* values starting with `[` or `{` as HCL,
        # everything else as a string — so HCL-flagged vars are written
        # verbatim by the operator and the executor doesn't need to re-quote.
        if db_session is not None:
            from app.services import variable_service as varsvc

            try:
                merged = await varsvc.get_merged_for_run(db_session, workspace.id, run)
            except Exception:
                logger.exception("Failed to build merged variable map for run %s", run.id)
                merged = {}
            for key, m in merged.items():
                environment[f"TF_VAR_{key}"] = m.env_value()
            if merged:
                # Audit-friendly: log keys + source, never values.
                logger.info(
                    "Run %s var keys: %s",
                    run.id,
                    {k: m.source for k, m in merged.items()},
                )

        # If the repo is bind-mounted on the API container at /mnt/local-repos
        # (dev-mode "local path" import), pass through the host path to the
        # executor so it can mount the same directory and skip `git clone`. The
        # entrypoint detects `local://<container-path>` and copies instead.
        volumes: dict[str, dict] = {}
        host_repos_dir = os.environ.get("TERRADUCKTEL_LOCAL_REPOS_HOST_DIR", "").strip()
        if repo_url.startswith("local://") and host_repos_dir:
            container_repos_dir = os.environ.get("TERRADUCKTEL_LOCAL_REPOS_DIR", "/mnt/local-repos")
            # Translate the local:// URL (which already encodes a container-side
            # path under /mnt/local-repos) into the equivalent host path so the
            # executor can mount it.
            container_path = repo_url[len("local://"):]
            if container_path.startswith(container_repos_dir):
                rel = container_path[len(container_repos_dir):].lstrip("/")
                host_path = os.path.join(host_repos_dir, rel) if rel else host_repos_dir
                volumes[host_path] = {"bind": "/workspace/repo", "mode": "ro"}
                # Tell the entrypoint the clone is already done.
                environment["REPO_PREMOUNTED"] = "1"

        # Mount the modules-registry local checkout if configured.
        if modules_local_volume is not None:
            host_mod, container_mod = modules_local_volume
            volumes[host_mod] = {"bind": container_mod, "mode": "ro"}

        # Network: the executor must reach `http://api:8000` (run-step PATCH +
        # Terraform HTTP state backend), so it has to land on the same compose
        # network as the API container. The compose project name is `terraducktel`,
        # so the default bridge network is `terraducktel_default`.
        network_name = os.environ.get("EXECUTOR_NETWORK", "terraducktel_default")

        # Plan phase: PENDING → RUNNING. Apply phase: the run is already in
        # APPLYING (set by approval_service.approve), so don't transition again.
        if phase == "plan" and run.status == RunStatus.PENDING:
            run.transition(RunStatus.RUNNING)

        # Two runtimes:
        # - `docker`: sibling-container via /var/run/docker.sock (local dev /
        #   docker-compose). The api spawns a container directly.
        # - `ecs`: calls ecs:RunTask on a pre-baked task definition. Used in
        #   the AWS production deploy where docker-in-docker is unavailable.
        # Pick via EXECUTOR_RUNTIME; default `docker` keeps the dev workflow
        # unchanged.
        runtime = os.environ.get("EXECUTOR_RUNTIME", "docker").strip().lower()

        if runtime == "ecs":
            return self._launch_via_ecs(run, environment)

        if self._docker is None:
            raise RuntimeError(
                "ExecutorService was constructed without a Docker client but "
                f"EXECUTOR_RUNTIME={runtime!r} requires one. Set EXECUTOR_RUNTIME=ecs "
                "or run the API on a host with /var/run/docker.sock available.",
            )

        # Hardening: the executor runs a workspace's own (semi-trusted,
        # possibly third-party) Terraform/Helm code — arbitrary providers,
        # `local-exec` provisioners, external data sources — with cloud
        # credentials injected as env vars. Treat it like the untrusted code
        # it is, same threat model as the internal-API token split above.
        #   - cap_drop / no-new-privileges: the image already runs as a
        #     non-root `USER executor` (see services/executor/Dockerfile), so
        #     it needs zero Linux capabilities and must never regain any via
        #     a setuid binary. Safe to apply unconditionally.
        #   - mem_limit / pids_limit: caps a runaway or malicious process
        #     (fork bomb, memory exhaustion) from starving the host. Sized
        #     generously for large plans; raise via env if you hit legitimate
        #     OOM kills on big state files.
        #   - Deliberately NOT setting read_only=True or a non-default `user`
        #     here: entrypoint.sh writes to /tmp, ~/.aws, ~/.kube, and the
        #     cloned working dir throughout the run, and the Dockerfile's
        #     USER already covers the privilege-drop half of that pairing.
        #     Doing read-only-root properly needs tmpfs mounts for each of
        #     those paths — worth a follow-up, not a drive-by change here.
        mem_limit = os.environ.get("EXECUTOR_MEM_LIMIT", "2g")
        pids_limit = int(os.environ.get("EXECUTOR_PIDS_LIMIT", "512"))

        kwargs: dict = dict(
            image=self.EXECUTOR_HELM_IMAGE if is_helm else self.EXECUTOR_IMAGE,
            environment=environment,
            detach=True,
            remove=False,
            network=network_name,
            mem_limit=mem_limit,
            pids_limit=pids_limit,
            cap_drop=["ALL"],
            # no-new-privileges is default-on hardening. A few host kernel /
            # container-runtime combos reject execve of the entrypoint when it's
            # set ("operation not permitted"), killing the executor before any
            # step runs. Allow opting out per-host via EXECUTOR_NO_NEW_PRIVS=false;
            # cap_drop=ALL + the non-root image user stay on regardless.
            security_opt=(
                ["no-new-privileges:true"]
                if os.environ.get("EXECUTOR_NO_NEW_PRIVS", "true").strip().lower()
                in ("1", "true", "yes")
                else []
            ),
        )
        if volumes:
            kwargs["volumes"] = volumes

        container = self._docker.containers.run(**kwargs)
        logger.info("Launched executor container %s for run %s", container.id[:12], run.id)
        return container

    def _launch_via_ecs(self, run: Any, environment: dict) -> Any:
        """Spawn an executor task using ecs:RunTask. Production path.

        Reads four targeting env vars set by terraform:
        - EXECUTOR_CLUSTER:   cluster ARN
        - EXECUTOR_TASK_DEF:  task definition family or ARN
        - EXECUTOR_SUBNETS:   comma-separated private subnet IDs
        - EXECUTOR_SG:        security group ID for the per-run task

        The plan-phase + apply-phase environment dict is passed via the
        RunTask `containerOverrides.environment` field — same env var names
        the entrypoint already reads.
        """
        import boto3

        cluster = os.environ.get("EXECUTOR_CLUSTER", "").strip()
        task_def = os.environ.get("EXECUTOR_TASK_DEF", "").strip()
        subnets = [s.strip() for s in os.environ.get("EXECUTOR_SUBNETS", "").split(",") if s.strip()]
        sg = os.environ.get("EXECUTOR_SG", "").strip()
        if not (cluster and task_def and subnets and sg):
            raise RuntimeError(
                "EXECUTOR_RUNTIME=ecs requires EXECUTOR_CLUSTER, EXECUTOR_TASK_DEF, "
                "EXECUTOR_SUBNETS, EXECUTOR_SG to be set",
            )

        ecs = boto3.client("ecs", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        # ECS expects each env var as {name, value}; coerce numeric run/workspace
        # IDs to str defensively.
        overrides_env = [{"name": k, "value": str(v)} for k, v in environment.items() if v is not None]

        resp = ecs.run_task(
            cluster=cluster,
            taskDefinition=task_def,
            launchType="FARGATE",
            count=1,
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": subnets,
                    "securityGroups": [sg],
                    "assignPublicIp": "DISABLED",
                },
            },
            overrides={
                "containerOverrides": [
                    {"name": "executor", "environment": overrides_env},
                ],
            },
            propagateTags="TASK_DEFINITION",
            startedBy=f"terraducktel-run-{run.id[:32]}",
        )
        failures = resp.get("failures") or []
        if failures:
            raise RuntimeError(f"ecs:RunTask failed: {failures}")
        tasks = resp.get("tasks") or []
        if not tasks:
            raise RuntimeError("ecs:RunTask returned no tasks")
        task_arn = tasks[0]["taskArn"]
        logger.info("Launched executor task %s for run %s", task_arn.split("/")[-1], run.id)
        # Return a duck-typed object so callers that only access `.id` keep
        # working without branching on runtime.
        return type("_EcsTask", (), {"id": task_arn, "task_arn": task_arn})()
