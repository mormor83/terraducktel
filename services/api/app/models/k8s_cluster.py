"""K8sCluster model — one row per Kubernetes cluster onboarded to Terraducktel.

Each cluster owns:
  - a human-readable name (unique per BU)
  - an optional API server URL + default namespace
  - a kubeconfig, encrypted at rest with Fernet (HKDF derived from
    CREDENTIAL_ENCRYPTION_KEY) — same scheme used by AwsAccount / ConfigService

Helm workspaces (Workspace.kind="helm") reference a cluster via
`Workspace.cluster_id`; helm releases live in-cluster so there is no external
state backend. The kubeconfig is NEVER logged and NEVER returned in API
responses — endpoints expose only a masked tail.
"""
import uuid

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class K8sCluster(Base):
    __tablename__ = "k8s_clusters"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Owning Business Unit. Cluster names are unique per BU, mirroring
    # aws_accounts — two BUs *can* register a cluster with the same name.
    business_unit_id: Mapped[str] = mapped_column(String, nullable=False)
    # Human-readable display name (e.g. "prod-eks"). Unique per BU.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional API server URL, surfaced for display / connectivity testing.
    server_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Default namespace used by helm runs when a workspace doesn't override it.
    default_namespace: Mapped[str | None] = mapped_column(String(120), nullable=True, default="default")
    # Optional AWS account (12-digit id) whose stored creds authenticate to the
    # cluster. EKS kubeconfigs use an exec plugin (`aws eks get-token`) that
    # needs AWS credentials — when set, the /test endpoint and helm runs export
    # this account's AWS_ACCESS_KEY_ID/SECRET so the plugin can mint a token.
    # Null for non-EKS clusters whose kubeconfig carries a static token/cert.
    aws_account_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # Encrypted kubeconfig. NEVER logged, NEVER returned in API responses.
    kubeconfig_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("business_unit_id", "name", name="uq_k8s_clusters_bu_name"),
    )
