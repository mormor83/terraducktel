import uuid
from sqlalchemy import Boolean, ForeignKey, String, Text, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class Workspace(Base):
    __tablename__ = "workspaces"
    # Canonical identity of a workspace within a BU is its (account, region,
    # environment, path) tuple — `name` is just a display label. Created in
    # migration 019; declared here so the metadata (and the SQLite test schema
    # built from it) matches the live DB and enforces the constraint.
    __table_args__ = (
        UniqueConstraint(
            "business_unit_id",
            "aws_account_id",
            "region",
            "environment",
            "tf_working_dir",
            name="uq_workspaces_bu_acc_region_env_path",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Owning Business Unit. Must match the BU of the linked AWS account
    # (enforced at the API layer on create/update).
    business_unit_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    aws_account_id: Mapped[str] = mapped_column(String(12), nullable=False)
    # Optional override for state-backend credentials. When set, the
    # executor uses this account's per-account creds for `AWS_ACCESS_KEY_ID`
    # / `AWS_SECRET_ACCESS_KEY` instead of `aws_account_id`'s. Used for
    # non-AWS workspaces (aws_account_id="global") whose terraform state
    # lives in an AWS S3 bucket — keeps the workspace grouped outside the
    # AWS account tree while still using same-account creds to open the
    # bucket. Null = behave as before (use aws_account_id's creds).
    state_aws_account_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # Optional Azure subscription FK. When set, the workspace targets the
    # azurerm provider; the executor exports the standard ARM_* env vars
    # from the linked subscription's encrypted SP credentials. Null means
    # AWS-only (the historical behaviour). State backend is still driven
    # by aws_account_id / state_aws_account_id — terraform state for Azure
    # workspaces continues to live in S3 for this release.
    azure_subscription_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("azure_subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    environment: Mapped[str] = mapped_column(String(50), nullable=False)
    region: Mapped[str] = mapped_column(String(50), nullable=False, default="us-east-1")
    repo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tf_working_dir: Mapped[str] = mapped_column(String(500), default=".")
    # Branch (or any git ref) the executor checks out for plan/apply runs.
    # Surfaced on the workspace tree as a tag and editable via the branch picker.
    repo_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    # Per-workspace opt-in for GitHub push webhooks. False by default so newly
    # imported workspaces stay quiet until an operator enables auto-trigger.
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    drift_status: Mapped[str] = mapped_column(String(20), default="unknown")
    # 'ok' = path exists in the repo at repo_ref. 'orphaned' = path was
    # removed/renamed in the source repo so plan/apply/destroy cannot
    # clone-and-cd successfully. 'unknown' = not yet checked. Updated by
    # the periodic repo_sync loop in main.lifespan + on-demand via
    # POST /v1/workspaces/{id}/sync.
    path_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown", server_default="unknown")
    path_status_checked_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Explicit S3 state-key suffix for new imports (set by discovery, see
    # services/repo_discovery.py). When non-null, `state_path` uses this
    # verbatim — lets two workspaces with the same `name` (e.g. `foo` in
    # `cust01/foo` and `cust02/foo`) live in different S3 keys without forcing
    # the joined-name workaround. Legacy workspaces have `state_key=NULL`
    # and continue to resolve via the `name`-based formula so their existing
    # tfstate stays where it is.
    state_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Workspace kind — gates terraform vs. helm behaviour throughout the
    # platform. Defaults to "terraform" so existing rows and any code path that
    # doesn't yet know about helm keeps behaving exactly as before. The
    # executor reads this (via a WORKSPACE_KIND env var) to map the
    # plan/apply/destroy command vocabulary onto helm equivalents.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="terraform")
    # For kind="helm": the target K8s cluster (k8s_clusters.id). Null for
    # terraform workspaces (and for helm workspaces not yet wired to a cluster).
    cluster_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def state_path(self) -> str:
        # Discovery sets `state_key` for new imports; old rows fall back to
        # the legacy {account}/{region}/{env}/{name} key so their existing
        # tfstate continues to resolve.
        if self.state_key:
            return f"tfstate/{self.state_key}/terraform.tfstate"
        return (
            f"tfstate/{self.aws_account_id}/{self.region}/"
            f"{self.environment}/{self.name}/terraform.tfstate"
        )
