"""Pydantic schemas for workspaces."""
import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# Allowed workspace kinds. "terraform" runs the TF plan/apply pipeline against
# an S3/HTTP state backend; "helm" runs helm diff/upgrade/uninstall against a
# k8s cluster (release state lives in-cluster, no external backend).
_WORKSPACE_KINDS = {"terraform", "helm"}


def _validate_kind(value: str) -> str:
    if value not in _WORKSPACE_KINDS:
        raise ValueError(
            f"kind must be one of {sorted(_WORKSPACE_KINDS)}, got {value!r}"
        )
    return value


# Allowed Terraform state backends. "s3" (default) resolves via aws_account_id;
# "azureblob" uses the linked azure_subscription's Blob container; "gcs" uses
# the linked gcp_project's GCS bucket. The API validates the required linkage
# on create/update (routers/workspaces.py).
_STATE_BACKENDS = {"s3", "azureblob", "gcs"}


def _validate_state_backend(value: str) -> str:
    if value not in _STATE_BACKENDS:
        raise ValueError(
            f"state_backend must be one of {sorted(_STATE_BACKENDS)}, got {value!r}"
        )
    return value


def _validate_repo_url(value: Optional[str]) -> Optional[str]:
    """Reject git transport-helper URLs.

    `ext::sh -c '...'` runs an arbitrary command and `file://` reads local
    paths when git allows those transports. We block the `transport::address`
    form and file/ext schemes at the API boundary; `GIT_ALLOW_PROTOCOL` in the
    clone subprocess env is the version-independent backstop. Normal forms
    (https://, http://, ssh://, scp-like git@host:path, and local://) pass.
    """
    if value is None or value == "":
        return value
    v = value.strip()
    lowered = v.lower()
    # Git transport-helper syntax is `helper::address` (e.g. `ext::sh -c …`) —
    # the RCE vector. Match a leading `<scheme>::` specifically (letters/digits/
    # +.- then `::`) rather than any `::`, so IPv6 literals like
    # `https://[::1]/repo.git` are NOT false-flagged. Also block the bare
    # file/ext/fd schemes.
    if re.match(r"^[a-z0-9][a-z0-9+.\-]*::", lowered) or lowered.startswith(
        ("ext:", "file:", "fd:")
    ):
        raise ValueError(
            "repo_url uses an unsupported git transport; use an https://, "
            "ssh://, or local:// URL"
        )
    return value


class WorkspaceCreate(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9\-_./]*$",
    )
    environment: str
    # Required for terraform workspaces; optional for helm (which targets a
    # cluster_id instead). The create endpoint enforces this per-kind and
    # defaults helm rows to the "global" sentinel account.
    aws_account_id: Optional[str] = None
    region: str = "us-east-1"
    repo_url: Optional[str] = None
    tf_working_dir: str = "."
    repo_ref: str = "main"
    webhook_enabled: bool = False
    # Workspace kind gates the executor's command interpretation. "terraform"
    # (default) keeps the existing pipeline; "helm" maps plan/apply/destroy to
    # helm diff/upgrade/uninstall and skips the S3/HTTP state backend.
    kind: str = "terraform"
    # Target k8s cluster for helm workspaces (FK into k8s_clusters). Null for
    # terraform workspaces.
    cluster_id: Optional[str] = None
    # Optional Azure subscription FK. When set, the workspace targets the
    # azurerm provider in addition to (or instead of) AWS; the executor
    # exports ARM_* env vars from the linked subscription's encrypted SP
    # credentials. State backend continues to be S3 for now.
    azure_subscription_id: Optional[str] = None
    # Optional GCP project FK. When set, the workspace targets the google
    # provider; the executor exports the linked project's SA-key credentials.
    gcp_project_id: Optional[str] = None
    # Where Terraform state is stored: "s3" (default), "azureblob", or "gcs".
    state_backend: str = "s3"

    @field_validator("kind")
    @classmethod
    def _kind_in_allowed(cls, value: str) -> str:
        return _validate_kind(value)

    @field_validator("state_backend")
    @classmethod
    def _state_backend_in_allowed(cls, value: str) -> str:
        return _validate_state_backend(value)

    @field_validator("repo_url")
    @classmethod
    def _repo_url_transport(cls, value: Optional[str]) -> Optional[str]:
        return _validate_repo_url(value)


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9\-_./]*$",
    )
    environment: Optional[str] = None
    aws_account_id: Optional[str] = None
    region: Optional[str] = None
    # Override for state-backend creds (see Workspace model). Pass an empty
    # string to clear back to the default (fall through to aws_account_id).
    state_aws_account_id: Optional[str] = None
    repo_url: Optional[str] = None
    tf_working_dir: Optional[str] = None
    repo_ref: Optional[str] = Field(default=None, min_length=1, max_length=255)
    webhook_enabled: Optional[bool] = None
    # Pass null (or omit) to leave unchanged; pass empty string to clear;
    # pass a value to relink to a different subscription.
    azure_subscription_id: Optional[str] = None
    # Same semantics as azure_subscription_id, for the GCP linkage.
    gcp_project_id: Optional[str] = None
    # Change where state is stored: "s3", "azureblob", or "gcs". The router
    # validates the required cloud linkage before applying the change.
    state_backend: Optional[str] = None

    @field_validator("state_backend")
    @classmethod
    def _state_backend_in_allowed(cls, value: Optional[str]) -> Optional[str]:
        return _validate_state_backend(value) if value is not None else value

    @field_validator("repo_url")
    @classmethod
    def _repo_url_transport(cls, value: Optional[str]) -> Optional[str]:
        return _validate_repo_url(value)


class WorkspaceResponse(BaseModel):
    id: str
    business_unit_id: str
    name: str
    environment: str
    aws_account_id: str
    region: str
    repo_url: Optional[str] = None
    tf_working_dir: str
    repo_ref: str = "main"
    webhook_enabled: bool = False
    # "terraform" (default) or "helm"; see WorkspaceCreate.kind.
    kind: str = "terraform"
    # Target k8s cluster FK for helm workspaces, else null.
    cluster_id: Optional[str] = None
    drift_status: str = "unknown"
    path_status: str = "unknown"
    path_status_checked_at: Optional[datetime] = None
    # Null = fall back to aws_account_id's creds for the state backend
    # (the legacy behavior). Set when the resource account differs from
    # the bucket-owning account (e.g. non-AWS workspaces).
    state_aws_account_id: Optional[str] = None
    # The PK of the linked azure_subscriptions row, or null for AWS-only.
    azure_subscription_id: Optional[str] = None
    # The PK of the linked gcp_projects row, or null if not a GCP workspace.
    gcp_project_id: Optional[str] = None
    # Where Terraform state is stored: "s3" (default), "azureblob", or "gcs".
    state_backend: str = "s3"
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Git-tree discovery / bulk import
# ---------------------------------------------------------------------------


class StackCandidateOut(BaseModel):
    path: str
    name: str
    aws_account_id: str
    region: str
    suggested_environment: str
    has_tf: bool = True
    # "terraform" or "helm" — derived from the on-disk signal (*.tf vs
    # Chart.yaml). The UI surfaces a Helm badge and routes helm imports to a
    # cluster instead of an AWS account.
    kind: str = "terraform"
    # True when this path is already a workspace in the current BU. UI uses
    # this to gray the row + uncheck it by default so the import dialog
    # doesn't ask the operator to re-import what's already there.
    already_imported: bool = False


class DiscoveryAccountOut(BaseModel):
    aws_account_id: str
    regions: dict[str, List[StackCandidateOut]]


class DiscoveryResultOut(BaseModel):
    repo_url: str
    ref: str
    accounts: List[DiscoveryAccountOut]
    stack_count: int
    errors: List[str]


class DiscoveryRequest(BaseModel):
    """Discovery input: either a remote repo (url + optional creds) or a local path.

    `local_path`, when provided, is scanned via `TERRADUCKTEL_LOCAL_REPOS_DIR` (a
    pre-configured mount on the API container). This is dev-only: it lets an
    operator point at an existing checkout without setting up SSH/PAT auth.
    Username/token are one-shot — they are used for the immediate clone only
    and never persisted by the discovery endpoint.
    """
    repo_url: Optional[str] = None
    ref: str = "main"
    username: Optional[str] = None
    token: Optional[str] = None
    local_path: Optional[str] = None

    @field_validator("repo_url")
    @classmethod
    def _repo_url_transport(cls, value: Optional[str]) -> Optional[str]:
        return _validate_repo_url(value)


class ImportEntry(BaseModel):
    """One stack to import from the discovery result."""
    path: str
    name: str
    aws_account_id: str
    region: str
    environment: str
    # Echoed back from discovery so the import endpoint can stamp
    # workspace.kind. Defaults to terraform for older UI clients.
    kind: str = "terraform"
    # Target k8s cluster for helm imports, else null.
    cluster_id: Optional[str] = None

    @field_validator("kind")
    @classmethod
    def _kind_in_allowed(cls, value: str) -> str:
        return _validate_kind(value)


class BulkImportRequest(BaseModel):
    repo_url: str = Field(..., min_length=1)
    ref: str = "main"
    entries: List[ImportEntry] = Field(..., min_length=1)

    @field_validator("repo_url")
    @classmethod
    def _repo_url_transport(cls, value: str) -> str:
        return _validate_repo_url(value)


class BulkImportResult(BaseModel):
    created: List[WorkspaceResponse]
    skipped: List[dict]  # {"path": ..., "reason": ...}
